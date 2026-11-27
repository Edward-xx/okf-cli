use chrono::{DateTime, Utc};
use okf_core::{classify, markdown::LinkTarget, resolve_path_field, BundleMetadata, Document};

#[test]
fn classification_preserves_format_failures_and_reserved_rules() {
    for raw in [
        "# README",
        "---\ntitle: Documentation\n---\n",
        "---\ntype: null\n---\n",
    ] {
        assert!(classify("README.md", raw).unwrap().is_none());
    }
    for raw in [
        "---\ntype: [\n---\n",
        "---\n- item\n---\n",
        "---\ntype: 23\n---\n",
        "---\ntype: Note\n",
    ] {
        assert!(classify("concept.md", raw).is_err());
    }
    let root = "---\nokf_version: '0.3'\ntitle: Test\ndescription: Test bundle\nlicense: MIT\nx-custom: {nested: true}\n---\n";
    assert!(classify("index.md", root).unwrap().is_none());
    assert!(classify("group/index.md", root).is_err());
    assert!(classify("log.md", root).is_err());
    assert!(classify(".hidden/concept.md", "bad yaml")
        .unwrap()
        .is_none());
    assert!(classify("asset.py", "not markdown").unwrap().is_none());
    assert_eq!(
        BundleMetadata::parse(root).unwrap().fields["x-custom"]["nested"],
        true
    );
}

#[test]
fn computation_sections_accept_nested_atx_levels_and_close_at_peers() {
    for (body, missing) in [
        ("## Computation\n```sql\nSELECT 1\n```\n", false),
        (
            "## Computation\n### Details\n```sql\nSELECT 1\n```\n",
            false,
        ),
        ("## Computation\n## Example\n```sql\nSELECT 1\n```\n", true),
        ("Computation\n===========\n```sql\nSELECT 1\n```\n", true),
    ] {
        let raw = format!("---\ntype: Attested Computation\nruntime: sql\nparameters: {{name: year, type: integer}}\n---\n{body}");
        let doc = Document::parse(&raw).unwrap();
        assert!(doc.diagnostics("compute.md").is_empty());
        let markdown = okf_core::markdown::Markdown::parse(
            &raw,
            doc.body_start,
            "compute.md",
            Some(&doc.frontmatter),
        );
        assert_eq!(
            markdown
                .diagnostics
                .iter()
                .any(|d| d.code == "MISSING_COMPUTATION"),
            missing
        );
    }
}

#[test]
fn source_descriptors_are_opaque_but_explicit_paths_remain_diagnostic() {
    let doc = Document::parse("---\ntype: Note\nsources:\n - {resource: 'all queries in project: finance'}\n - {resource: 'policy docs.md'}\n - {resource: './invalid:path with spaces.md'}\n---\n").unwrap();
    let fields = okf_core::path_fields(&doc);
    assert_eq!(fields.len(), 2);
    assert_eq!(fields[0], ("/sources/1/resource".into(), "policy docs.md"));
    assert!(okf_core::resolve_path_field(
        "source.md",
        fields[1].1,
        &BundleMetadata::default(),
        |_| Ok(false)
    )
    .is_err());
}

#[test]
fn directory_syntax_survives_path_normalization() {
    for value in [
        "target.md/",
        "target.md%2F",
        "target.md/.",
        "../target.md%2f#part",
    ] {
        assert!(
            okf_core::markdown::directory_reference("nested/source.md", value).unwrap(),
            "{value}"
        );
    }
    for value in ["target.md", "target.md#section/", "target.md?query=/"] {
        assert!(
            !okf_core::markdown::directory_reference("nested/source.md", value).unwrap(),
            "{value}"
        );
    }
}

#[test]
fn version_matrix_and_fallback_only_on_absence() {
    for (version, legacy) in [
        (None, true),
        (Some("0.2"), true),
        (Some("0.3"), false),
        (Some("99.0"), false),
    ] {
        let raw = version
            .map(|v| format!("---\nokf_version: '{v}'\n---\n"))
            .unwrap_or_default();
        let metadata = BundleMetadata::parse(&raw).unwrap();
        assert_eq!(metadata.legacy_paths(), legacy);
        let path = |target| match target {
            LinkTarget::Internal { path, .. } => path,
            _ => panic!(),
        };
        assert_eq!(
            path(resolve_path_field("nested/a.md", "target.md", &metadata, |_| Ok(true)).unwrap()),
            "nested/target.md"
        );
        assert_eq!(
            path(
                resolve_path_field("nested/a.md", "target.md", &metadata, |p| Ok(
                    p == "target.md"
                ))
                .unwrap()
            ),
            if legacy {
                "target.md"
            } else {
                "nested/target.md"
            }
        );
        assert_eq!(
            path(
                resolve_path_field("nested/a.md", "/target.md#part", &metadata, |_| Ok(true))
                    .unwrap()
            ),
            "target.md"
        );
        assert_eq!(
            path(
                resolve_path_field("nested/a.md", "../target.md", &metadata, |_| Ok(true)).unwrap()
            ),
            "target.md"
        );
        assert!(
            resolve_path_field("nested/a.md", "../../secret", &metadata, |_| panic!(
                "must not stat escape"
            ))
            .is_err()
        );
        if legacy {
            assert!(
                resolve_path_field("nested/a.md", "target.md", &metadata, |_| Err(
                    okf_core::Error::new("PATH_ESCAPE", "symlink", 4)
                ))
                .is_err()
            );
        }
        assert_eq!(
            metadata.diagnostics().len(),
            usize::from(version == Some("99.0"))
        );
    }
}

#[test]
fn dates_actors_empty_values_and_single_items_share_consumption_rules() {
    let now = DateTime::parse_from_rfc3339("2026-11-27T00:00:00Z")
        .unwrap()
        .with_timezone(&Utc);
    for actor in [
        "team:finance",
        "custom:opaque/id",
        "process:nightly",
        "human:alice",
    ] {
        let raw = format!("---\ntype: Note\ntags: 'one,two'\nstatus: null\ntitle: ''\ngenerated: {{by: worker/1, at: 2026-11-26}}\nverified: {{by: '{actor}', at: '2026-11-27'}}\nstale_after: 2026-11-27\nsources: {{resource: 'all queries in project X', author: 'team:finance'}}\n---\noriginal bytes\n");
        let doc = Document::parse(&raw).unwrap();
        let card = doc.card("note", now);
        assert_eq!(card.tags, ["one,two"]);
        assert_eq!(card.status, "stable");
        assert_eq!(card.title, "note");
        assert_eq!(card.expiry, "expired");
        assert_eq!(
            card.trust,
            if actor.starts_with("human:") {
                "human-reviewed"
            } else {
                "machine-confirmed"
            }
        );
        assert!(doc.diagnostics("note.md").is_empty());
        assert!(doc.frontmatter["tags"].is_string());
        assert_eq!(&raw[doc.body_start..], "original bytes\n");
    }
    let doc = Document::parse("---\ntype: Note\ngenerated: {by: worker/1, at: '2026-11-27T00:00:01Z'}\nverified: {by: 'human:alice', at: 2026-11-27}\n---\n").unwrap();
    assert_eq!(doc.card("note", now).trust, "unverified");
    for date in ["2026-02-29", "2026-1-01", "2026-11-27T00:00:00"] {
        let doc =
            Document::parse(&format!("---\ntype: Note\nstale_after: '{date}'\n---\n")).unwrap();
        assert_eq!(doc.card("note", now).expiry, "unknown");
    }
}
