use crate::{BundleBinding, BundleReader, RequestControl, Response};
use okf_core::markdown::LinkTarget;
use okf_core::{Diagnostic, Document};
use serde_json::json;

const MAX_BYTES: usize = 2 * 1024 * 1024;
pub(crate) struct Resources {
    pending: Vec<(String, String, String)>,
    bytes: usize,
    limit: usize,
    truncated: bool,
}
impl Resources {
    pub fn new(limit: usize) -> Self {
        Self {
            pending: vec![],
            bytes: 0,
            limit,
            truncated: false,
        }
    }
    pub fn collect(&mut self, path: &str, doc: &Document) {
        if doc.frontmatter.get("type").and_then(|v| v.as_str()) != Some("Attested Computation") {
            return;
        }
        for (field, value) in [
            ("/computation", doc.frontmatter.get("computation")),
            (
                "/executor/resource",
                doc.frontmatter
                    .get("executor")
                    .and_then(|v| v.get("resource")),
            ),
            (
                "/attester/resource",
                doc.frontmatter
                    .get("attester")
                    .and_then(|v| v.get("resource")),
            ),
        ] {
            if let Some(value) = value
                .and_then(|v| v.as_str())
                .filter(|v| !v.trim().is_empty())
            {
                let bytes = path.len() + field.len() + value.len();
                if self.pending.len() >= self.limit || bytes > MAX_BYTES - self.bytes {
                    self.truncated = true;
                } else {
                    self.bytes += bytes;
                    self.pending.push((path.into(), field.into(), value.into()));
                }
            }
        }
    }
    pub fn check(
        self,
        metadata: &okf_core::BundleMetadata,
        binding: &BundleBinding,
        reader: &dyn BundleReader,
        control: &RequestControl,
        response: &mut Response,
        documents_complete: bool,
    ) {
        let mut complete = documents_complete && !self.truncated;
        let mut checked = 0;
        let mut external = 0;
        for (path, field, resource) in self.pending {
            if let Err(error) = control.check() {
                complete = false;
                if !response.diagnostics.iter().any(|d| d.code == error.code) {
                    response.partial(&error.code, &error.message);
                }
                break;
            }
            let reference = format!("@{}/{}", binding.name, path);
            let resolved = okf_core::resolve_path_field(&path, &resource, metadata, |candidate| {
                reader
                    .stat(binding, candidate, control)
                    .map(|v| v.is_some())
            });
            let directory =
                okf_core::markdown::directory_reference(&path, &resource).unwrap_or(false);
            let finding = match resolved {
                Ok(LinkTarget::External) => {
                    external += 1;
                    continue;
                }
                Err(error)
                    if matches!(
                        error.code.as_str(),
                        "INVALID_LINK_PATH" | "LINK_PATH_ESCAPE"
                    ) =>
                {
                    Some(("INVALID_RESOURCE_PATH", error.message))
                }
                Err(error) => {
                    complete = false;
                    response.issue(&error, &reference);
                    if crate::interrupted(&error) {
                        break;
                    }
                    continue;
                }
                Ok(LinkTarget::Internal { path, .. }) => {
                    checked += 1;
                    match reader.stat(binding, &path, control) {
                        Ok(Some(meta)) if !meta.directory && !directory => None,
                        Ok(Some(_)) => Some((
                            "RESOURCE_NOT_A_FILE",
                            format!("declared resource must identify a file: {resource}"),
                        )),
                        Ok(None) => Some((
                            "MISSING_RESOURCE",
                            format!("declared resource does not exist: {resource}"),
                        )),
                        Err(error) => {
                            complete = false;
                            response.issue(&error, &reference);
                            if crate::interrupted(&error) {
                                break;
                            }
                            continue;
                        }
                    }
                }
            };
            if let Some((code, message)) = finding {
                let mut diagnostic = Diagnostic::warning(code, message, Some(&field));
                diagnostic.path = Some(reference);
                response.diagnostics.push(diagnostic);
            }
        }
        if self.truncated {
            response.partial("RESOURCE_LIMIT", "resource count or byte budget reached");
        }
        response.data["resource_check"] = json!({"scope":"bundle_computation_resources","complete":complete,"external_skipped":external});
        response.budget["resource_limit"] = json!(self.limit);
        response.budget["resources_checked"] = json!(checked);
        response.budget["resource_bytes_limit"] = json!(MAX_BYTES);
        response.budget["resource_bytes_used"] = json!(self.bytes);
    }
}
