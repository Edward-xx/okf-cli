use chrono::DateTime;
use okf_core::Document;

#[test]
fn only_complete_current_events_contribute_to_trust() {
    let generated = "generated: {by: agent/1, at: '2026-11-20T00:00:00Z'}\n";
    for (update, events, expected) in [
        (generated, "{by: 'human:alice', at: '2026-10-01T00:00:00Z'}", "unverified"),
        (generated, "{by: 'human:alice', at: '2026-11-20T00:00:00Z'}", "human-reviewed"),
        (generated, "{by: 'human:alice', at: '2026-11-21T00:00:00Z'}", "human-reviewed"),
        (generated, "{by: 'human:alice', at: '2026-11-20T08:00:00+08:00'}", "human-reviewed"),
        (generated, "{by: 'human:alice', at: '2026-11-20T01:00:00+08:00'}", "unverified"),
        ("", "{by: 'human:alice', at: '2026-10-01T00:00:00Z'}", "human-reviewed"),
        ("", "{by: 'human:alice'}", "unverified"),
        ("", "{by: 'human:alice', at: nope}", "unverified"),
        ("", "{by: 'human:alice', at: null}", "unverified"),
        ("", "{by: 'human:alice', at: 123}", "unverified"),
        ("", "{at: '2026-11-21T00:00:00Z'}", "unverified"),
        ("", "{by: 'human:', at: '2026-11-21T00:00:00Z'}", "unverified"),
        (generated, "[{by: 'human:alice', at: '2026-10-01T00:00:00Z'}, {by: 'process:ci', at: '2026-11-21T00:00:00Z'}]", "machine-confirmed"),
        (generated, "[{by: 'human:alice', at: '2026-11-20T00:00:00Z'}, {by: 'process:ci', at: '2026-11-21T00:00:00Z'}]", "human-reviewed"),
        (generated, "[null, 1, {by: 'human:alice'}, {by: agent/1, at: '2026-11-21T00:00:00Z'}]", "machine-confirmed"),
        (generated, "[]", "unverified"),
    ] {
        let raw = format!("---\ntype: Note\n{update}verified: {events}\n---\nbody\n");
        let doc = Document::parse(&raw).unwrap();
        let original = doc.frontmatter.clone();
        assert_eq!(doc.card("note", DateTime::UNIX_EPOCH).trust, expected, "{raw}");
        assert_eq!(doc.frontmatter, original);
        assert_eq!(&raw[doc.body_start..], "body\n");
    }
}

#[test]
fn unknown_content_update_time_cannot_imply_current_verification() {
    for generated in [
        "null",
        "true",
        "[]",
        "{}",
        "{by: agent/1}",
        "{by: agent/1, at: nope}",
    ] {
        let raw = format!("---\ntype: Note\ngenerated: {generated}\nverified: {{by: 'human:alice', at: '2026-11-21T00:00:00Z'}}\n---\n");
        let doc = Document::parse(&raw).unwrap();
        assert_eq!(doc.card("note", DateTime::UNIX_EPOCH).trust, "unverified");
        assert!(doc
            .diagnostics("note.md")
            .iter()
            .any(|d| d.code == "INDETERMINATE_VERIFICATION_TIME"));
    }
}
