//! OKF v0.3 consumption rules. Original YAML and document bytes stay untouched.
use crate::{
    frontmatter,
    markdown::{resolve_link, LinkTarget},
    Diagnostic, Document, Error, Result,
};
use serde_json::{Map, Value};

pub fn excluded_path(path: &str) -> bool {
    path.split('/')
        .any(|part| part.starts_with('.') && part != "." && part != "..")
}

pub fn present(value: &Value) -> bool {
    !value.is_null()
        && !value.as_str().is_some_and(str::is_empty)
        && !value.as_array().is_some_and(Vec::is_empty)
}

pub fn items(value: Option<&Value>) -> &[Value] {
    match value.filter(|v| present(v)) {
        None => &[],
        Some(Value::Array(values)) => values,
        Some(value) => std::slice::from_ref(value),
    }
}

/// A consumption view, never serialized back to the source document.
pub(crate) fn normalized(map: &Map<String, Value>) -> Map<String, Value> {
    let mut map = map.clone();
    for key in [
        "title",
        "description",
        "resource",
        "tags",
        "generated",
        "verified",
        "sources",
        "usage_window",
        "status",
        "stale_after",
        "superseded_by",
        "runtime",
        "parameters",
        "computation",
        "executor",
        "attester",
    ] {
        if map.get(key).is_some_and(|v| !present(v)) {
            map.remove(key);
        }
    }
    for key in ["tags", "sources", "parameters"] {
        if let Some(value) = map.get_mut(key) {
            if !value.is_array() {
                *value = Value::Array(vec![value.clone()]);
            }
        }
    }
    fn clean(value: &mut Value, keys: &[&str]) {
        if let Some(object) = value.as_object_mut() {
            for key in keys {
                if object.get(*key).is_some_and(|v| !present(v)) {
                    object.remove(*key);
                }
            }
        }
    }
    for key in ["generated", "verified"] {
        if let Some(value) = map.get_mut(key) {
            if let Some(values) = value.as_array_mut() {
                for value in values {
                    clean(value, &["by", "at"]);
                }
            } else {
                clean(value, &["by", "at"]);
            }
        }
    }
    for key in ["sources", "parameters"] {
        if let Some(Value::Array(values)) = map.get_mut(key) {
            for value in values {
                clean(
                    value,
                    if key == "sources" {
                        &[
                            "id",
                            "resource",
                            "title",
                            "author",
                            "last_modified",
                            "usage_count",
                            "usage_window",
                        ]
                    } else {
                        &["name", "type", "required"]
                    },
                );
                if let Some(window) = value.get_mut("usage_window") {
                    clean(window, &["from", "to"]);
                }
            }
        }
    }
    if let Some(window) = map.get_mut("usage_window") {
        clean(window, &["from", "to"]);
    }
    for key in ["executor", "attester"] {
        if let Some(value) = map.get_mut(key) {
            clean(value, &["resource", "receipt"]);
        }
    }
    map
}

/// None is a non-concept; malformed declared frontmatter remains an error.
pub fn classify(path: &str, raw: &str) -> Result<Option<Document>> {
    if excluded_path(path) || !path.ends_with(".md") {
        return Ok(None);
    }
    if crate::reserved(path) {
        crate::validate_reserved(path, raw)?;
        return Ok(None);
    }
    if !has_frontmatter(raw) {
        return Ok(None);
    }
    let (map, body_start) = frontmatter(raw)?;
    if map.get("type").is_none_or(|v| !present(v)) {
        return Ok(None);
    }
    if map
        .get("type")
        .and_then(Value::as_str)
        .is_none_or(|s| s.trim().is_empty())
    {
        return Err(Error::new(
            "INVALID_OKF",
            "type must be a non-empty string",
            1,
        ));
    }
    Ok(Some(Document {
        frontmatter: map,
        body_start,
    }))
}

pub fn has_frontmatter(raw: &str) -> bool {
    raw.trim_start_matches('\u{feff}')
        .lines()
        .next()
        .is_some_and(|s| s.trim_end() == "---")
}

#[derive(Debug, Default, Clone)]
pub struct BundleMetadata {
    pub fields: Map<String, Value>,
}
impl BundleMetadata {
    pub fn parse(raw: &str) -> Result<Self> {
        crate::validate_reserved("index.md", raw)?;
        Ok(Self {
            fields: if has_frontmatter(raw) {
                frontmatter(raw)?.0
            } else {
                Map::new()
            },
        })
    }
    pub fn legacy_paths(&self) -> bool {
        match self.fields.get("okf_version").filter(|v| present(v)) {
            None => true,
            Some(Value::String(v)) => matches!(v.as_str(), "0.1" | "0.2" | "0.0"),
            _ => false,
        }
    }
    pub fn diagnostics(&self) -> Vec<Diagnostic> {
        let mut out = vec![];
        if let Some(version) = self.fields.get("okf_version").filter(|v| present(v)) {
            if !version
                .as_str()
                .is_some_and(|v| matches!(v, "0.0" | "0.1" | "0.2" | "0.3"))
            {
                out.push(Diagnostic::warning("UNKNOWN_OKF_VERSION", "unknown OKF version; using v0.3 best-effort consumption without legacy path fallback", Some("/okf_version")));
            }
        }
        for name in ["title", "description", "license"] {
            if self
                .fields
                .get(name)
                .filter(|v| present(v))
                .is_some_and(|v| !v.is_string())
            {
                out.push(Diagnostic::warning(
                    "INVALID_METADATA_TYPE",
                    "expected a string",
                    Some(&format!("/{name}")),
                ));
            }
        }
        for d in &mut out {
            d.path = Some("index.md".into());
        }
        out
    }
}

/// Only absence permits legacy fallback. Errors (including access/escape) propagate.
pub fn resolve_path_field(
    document: &str,
    value: &str,
    metadata: &BundleMetadata,
    mut exists: impl FnMut(&str) -> Result<bool>,
) -> Result<LinkTarget> {
    let target = resolve_link(document, value)?;
    if let LinkTarget::Internal { path, .. } = &target {
        if excluded_path(path) {
            return Ok(LinkTarget::External);
        }
        if metadata.legacy_paths() && !value.starts_with('/') && !exists(path)? {
            // A file-relative path may legitimately start with ../; the optional
            // root interpretation must not turn that into an escape diagnostic.
            let Ok(fallback) = resolve_link("index.md", value) else {
                return Ok(target);
            };
            if let LinkTarget::Internal {
                path: fallback_path,
                ..
            } = &fallback
            {
                if !excluded_path(fallback_path) && exists(fallback_path)? {
                    return Ok(fallback);
                }
            }
        }
    }
    Ok(target)
}

/// Standard concept path fields, retaining JSON pointers as evidence.
pub fn path_fields(doc: &Document) -> Vec<(String, &str)> {
    let mut fields = vec![];
    for name in ["resource", "computation", "superseded_by"] {
        if name == "superseded_by"
            && doc.frontmatter.get("status").and_then(Value::as_str) != Some("deprecated")
        {
            continue;
        }
        if let Some(value) = doc
            .frontmatter
            .get(name)
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
        {
            fields.push((format!("/{name}"), value));
        }
    }
    for name in ["executor", "attester"] {
        if let Some(value) = doc
            .frontmatter
            .get(name)
            .and_then(|v| v.get("resource"))
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
        {
            fields.push((format!("/{name}/resource"), value));
        }
    }
    for (i, source) in items(doc.frontmatter.get("sources"))
        .iter()
        .take(256)
        .enumerate()
    {
        if let Some(value) = source
            .get("resource")
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
        {
            // Provenance also permits opaque population/scope descriptions.
            // A prose descriptor may contain ':' or other non-path characters.
            // Explicit paths must still receive normal path diagnostics.
            if value.chars().any(char::is_whitespace)
                && !value.starts_with(['/', '.'])
                && resolve_link("index.md", value).is_err()
            {
                continue;
            }
            let field = if doc.frontmatter.get("sources").is_some_and(Value::is_array) {
                format!("/sources/{i}/resource")
            } else {
                "/sources/resource".into()
            };
            fields.push((field, value));
        }
    }
    fields
}

pub fn not_concept() -> Error {
    Error::new("NOT_CONCEPT", "file is not an OKF concept", 3)
}
