//! Direct, evidenced relationships from one published index generation.
use crate::{App, Index, ReadOptions, Response};
use okf_core::{
    markdown::{LinkKind, LinkTarget, Markdown},
    Error, Result,
};
use serde_json::json;
use std::collections::BTreeSet;

impl App<'_> {
    pub fn neighbors(
        &self,
        reference: &str,
        direction: &str,
        options: &ReadOptions,
        index: &dyn Index,
    ) -> Result<Response> {
        let (name, id) = reference
            .strip_prefix('@')
            .and_then(|s| s.split_once('/'))
            .ok_or_else(|| Error::new("INVALID_REFERENCE", "expected @bundle/path", 2))?;
        let path = okf_core::logical_path(id)?;
        if okf_core::excluded_path(&path) {
            return Err(Error::new(
                "NOT_FOUND",
                "dot paths are outside the bundle",
                3,
            ));
        }
        let path = if path.ends_with(".md") {
            path
        } else {
            format!("{path}.md")
        };
        let (binding, snapshot) = self.snapshot(name, index)?;
        if let Some(directories) = &snapshot.directories {
            let generation = crate::inventory_generation(
                snapshot.upstream_revision.as_deref(),
                snapshot
                    .documents
                    .iter()
                    .map(|d| (d.path.as_str(), d.hash.as_str())),
                directories,
            )?;
            if generation != snapshot.generation {
                return Err(Error::new(
                    "INDEX_CORRUPT",
                    "document or directory inventory differs from snapshot; run sync",
                    5,
                ));
            }
        }
        let paths: BTreeSet<_> = snapshot.documents.iter().map(|d| d.path.as_str()).collect();
        let directories: BTreeSet<_> = snapshot
            .directories
            .as_deref()
            .unwrap_or_default()
            .iter()
            .map(String::as_str)
            .collect();
        let exists = |p: &str| Ok(paths.contains(p) || directories.contains(p));
        let metadata = snapshot
            .documents
            .iter()
            .find(|d| d.path == "index.md")
            .map(|d| okf_core::BundleMetadata::parse(&d.raw))
            .transpose()?
            .unwrap_or_default();
        if metadata.legacy_paths() && snapshot.directories.is_none() {
            return Err(Error::new(
                "INDEX_INVENTORY_MISSING",
                "legacy path fallback requires directory inventory; run sync",
                3,
            ));
        }
        let mut parsed = std::collections::BTreeMap::new();
        for d in &snapshot.documents {
            self.control.check()?;
            if let Some(doc) = okf_core::classify(&d.path, &d.raw)? {
                parsed.insert(d.path.as_str(), doc);
            }
        }
        let concepts: BTreeSet<_> = parsed.keys().copied().collect();
        if !paths.contains(path.as_str()) {
            return Err(Error::new("NOT_FOUND", "file absent from index", 3));
        }
        let mut response = Response::new(
            "neighbors",
            json!({"target":reference,"direction":direction,"generation":snapshot.generation,"items":[]}),
        );
        response.diagnostics.extend(metadata.diagnostics());
        let mut items = vec![];
        let mut used = 0;
        let mut scanned = 0;
        let mut total = 0;
        let mut complete = true;
        for document in &snapshot.documents {
            if direction == "outgoing" && document.path != path {
                continue;
            }
            if let Err(e) = self.control.check() {
                response.partial(&e.code, &e.message);
                complete = false;
                break;
            }
            if scanned >= options.max_scan {
                response.partial("SCAN_LIMIT", "relationship scan budget reached");
                complete = false;
                break;
            }
            scanned += 1;
            let Some(parsed_doc) = parsed.get(document.path.as_str()) else {
                continue;
            };
            let markdown = Markdown::parse(
                &document.raw,
                parsed_doc.body_start,
                &document.path,
                Some(&parsed_doc.frontmatter),
            );
            if markdown.truncated {
                complete = false;
            }
            for diagnostic in &markdown.diagnostics {
                if matches!(
                    diagnostic.code.as_str(),
                    "INVALID_LINK_PATH" | "LINK_PATH_ESCAPE"
                ) {
                    complete = false;
                    response.issue(
                        &Error::new(&diagnostic.code, &diagnostic.message, 7),
                        &format!("@{name}/{}", document.path),
                    );
                }
            }
            let mut edges = vec![];
            for link in markdown.links {
                if let LinkTarget::Internal {
                    path: target,
                    fragment,
                } = link.target
                {
                    if link.kind == LinkKind::Link && !link.directory && target.ends_with(".md") {
                        edges.push((target, json!({"kind":"markdown_link","destination":link.destination,"fragment":fragment,"location":link.location})));
                    }
                }
            }
            if okf_core::items(parsed_doc.frontmatter.get("sources")).len() > 256 {
                complete = false;
            }
            for (field, resource) in okf_core::path_fields(parsed_doc) {
                match okf_core::resolve_path_field(&document.path, resource, &metadata, exists) {
                    Ok(LinkTarget::Internal {
                        path: target,
                        fragment,
                    }) => {
                        if okf_core::markdown::directory_reference(&document.path, resource)
                            .unwrap_or(false)
                        {
                            if field == "/superseded_by" {
                                let mut warning = okf_core::Diagnostic::warning(
                                    "INVALID_SUPERSESSION_TARGET",
                                    "replacement must name a concept file, not a directory",
                                    Some(&field),
                                );
                                warning.path = Some(format!("@{name}/{}", document.path));
                                response.diagnostics.push(warning);
                            }
                            continue;
                        }
                        if concepts.contains(target.as_str())
                            || field == "/superseded_by"
                            || target.ends_with(".md")
                        {
                            let kind = if field == "/superseded_by" {
                                "superseded_by"
                            } else if field.starts_with("/sources/") {
                                "source"
                            } else {
                                "path_field"
                            };
                            edges.push((target, json!({"kind":kind,"destination":resource,"fragment":fragment,"field":field})));
                        }
                    }
                    Err(error) => {
                        response.issue(&error, &format!("@{name}/{}", document.path));
                        complete = false;
                    }
                    _ => {}
                }
            }
            for (target, evidence) in edges {
                let outgoing = document.path == path;
                let incoming = target == path;
                if !(outgoing && direction != "incoming" || incoming && direction != "outgoing") {
                    continue;
                }
                if !concepts.contains(target.as_str()) {
                    let mut warning = okf_core::Diagnostic::warning(
                        if evidence["kind"] == "superseded_by" {
                            "INVALID_SUPERSESSION_TARGET"
                        } else {
                            "NON_CONCEPT_TARGET"
                        },
                        format!("reference does not resolve to an indexed concept: {target}"),
                        evidence["field"].as_str(),
                    );
                    warning.path = Some(format!("@{name}/{}", document.path));
                    response.diagnostics.push(warning);
                    continue;
                }
                total += 1;
                let item = json!({"from":format!("@{name}/{}",document.path),"to":format!("@{name}/{target}"),
                    "direction":if outgoing {"outgoing"} else {"incoming"},"target_exists":paths.contains(target.as_str()),
                    "generation":snapshot.generation,"upstream_revision":snapshot.upstream_revision,
                    "evidence":{"path":document.path,"content_sha256":document.hash,"reference":evidence}});
                let size = serde_json::to_vec(&item).map_err(Error::io)?.len();
                if items.len() < options.limit && size <= options.budget.saturating_sub(used) {
                    used += size;
                    items.push(item);
                }
            }
        }
        // Diagnose only the requested concept's replacement chain, with its own
        // disclosed bound. Never rewrite any source edge or stored reference.
        let mut chain = BTreeSet::new();
        let mut at = path.clone();
        let mut lifecycle_scanned = 0;
        let mut lifecycle_complete = true;
        while let Some(doc) = parsed.get(at.as_str()) {
            if let Err(error) = self.control.check() {
                lifecycle_complete = false;
                response.partial(&error.code, &error.message);
                break;
            }
            if !chain.insert(at.clone()) {
                let mut d = okf_core::Diagnostic::warning(
                    "SUPERSESSION_CYCLE",
                    "replacement chain contains a cycle",
                    Some("/superseded_by"),
                );
                d.path = Some(format!("@{name}/{at}"));
                response.diagnostics.push(d);
                break;
            }
            if lifecycle_scanned >= options.max_scan {
                lifecycle_complete = false;
                response.partial("SCAN_LIMIT", "replacement chain scan budget reached");
                break;
            }
            lifecycle_scanned += 1;
            let fields = okf_core::path_fields(doc);
            let Some((_, value)) = fields.iter().find(|(field, _)| field == "/superseded_by")
            else {
                break;
            };
            match okf_core::resolve_path_field(&at, value, &metadata, exists) {
                Ok(LinkTarget::Internal { path: target, .. })
                    if concepts.contains(target.as_str())
                        && !okf_core::markdown::directory_reference(&at, value)
                            .unwrap_or(false) =>
                {
                    at = target
                }
                _ => {
                    let mut d = okf_core::Diagnostic::warning(
                        "INVALID_SUPERSESSION_TARGET",
                        format!("replacement does not resolve to a concept: {value}"),
                        Some("/superseded_by"),
                    );
                    d.path = Some(format!("@{name}/{at}"));
                    if !response
                        .diagnostics
                        .iter()
                        .any(|old| old.code == d.code && old.path == d.path)
                    {
                        response.diagnostics.push(d);
                    }
                    break;
                }
            }
        }
        complete &= lifecycle_complete;
        response.data["supersession_check"] = json!({"complete":lifecycle_complete,"scope":path,"documents_checked":lifecycle_scanned,"scan_limit":options.max_scan});
        if !complete && response.status == "ok" {
            response.partial("REFERENCE_LIMIT", "relationship references were truncated");
        }
        if items.len() < total {
            response.partial(
                "RESULT_LIMIT",
                "not all relationships fit the result budget",
            );
        }
        response.page["returned"] = json!(items.len());
        response.page["total"] = if complete {
            json!(total)
        } else {
            serde_json::Value::Null
        };
        let mut next_targets = BTreeSet::new();
        for item in &items {
            let target = if item["direction"] == "incoming" {
                item["from"].as_str()
            } else if item["target_exists"] == true {
                item["to"].as_str()
            } else {
                None
            };
            if let Some(target) = target {
                if next_targets.insert(target.to_owned()) {
                    response.next.push(json!({"operation":"read","target":target,"indexed":true,"generation":snapshot.generation}));
                }
            }
        }
        response.data["items"] = json!(items);
        response.data["relationship_check"] = json!({"complete":complete,"scope":if direction == "outgoing" {"document"} else {"bundle"},"source_paths":"file_relative_with_versioned_fallback"});
        response.budget = json!({"scan_limit":options.max_scan,"indexed_documents_scanned":scanned,"inventory_documents_classified":snapshot.documents.len(),"content_bytes_limit":options.budget,"content_bytes_used":used});
        crate::index::indexed_observation(&mut response, &binding, &snapshot, complete);
        response.bundles[0]["coverage"]["graph"] =
            json!(if complete { "complete" } else { "partial" });
        response.bundles[0]["coverage"]["scope"] = json!(if direction == "outgoing" {
            path.as_str()
        } else {
            ""
        });
        Ok(response)
    }
}
