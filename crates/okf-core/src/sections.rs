//! Parsed heading boundaries shared by excerpts and opt-in anchor checking.
use pulldown_cmark::{Event, Parser, Tag, TagEnd};
use std::collections::BTreeSet;

pub const MAX_HEADINGS: usize = 4096;
#[derive(Debug)]
pub struct Heading {
    pub start: usize,
    pub end: usize,
    pub level: u8,
    pub anchor: String,
}
#[derive(Debug, Default)]
pub struct Sections {
    pub headings: Vec<Heading>,
    pub complete: bool,
    pub custom_html: bool,
    pub html_anchors: BTreeSet<String>,
}
impl Sections {
    pub fn parse(raw: &str, body_start: usize) -> Self {
        let mut result = Self {
            complete: true,
            ..Self::default()
        };
        let mut current = None;
        let mut title = String::new();
        let mut anchors = BTreeSet::new();
        let mut html_block_start = None;
        let mut raw_element = None;
        for (event, range) in Parser::new(&raw[body_start..]).into_offset_iter() {
            match event {
                Event::Start(Tag::Heading { level, .. }) => {
                    if result.headings.len() == MAX_HEADINGS {
                        result.complete = false;
                        break;
                    }
                    current = Some((body_start + range.start, level as u8));
                    title.clear();
                }
                Event::Text(text) | Event::Code(text) if current.is_some() => title.push_str(&text),
                Event::SoftBreak | Event::HardBreak if current.is_some() => title.push(' '),
                Event::End(TagEnd::Heading(_)) => {
                    if let Some((start, level)) = current.take() {
                        // Explicit headings-v1 profile, not a promise about renderer-specific IDs.
                        let base: String = title
                            .to_lowercase()
                            .chars()
                            .filter_map(|c| {
                                if c.is_whitespace() {
                                    Some('-')
                                } else if c.is_alphanumeric() || matches!(c, '_' | '-') {
                                    Some(c)
                                } else {
                                    None
                                }
                            })
                            .collect();
                        let mut anchor = base.clone();
                        let mut suffix = 0;
                        while !anchors.insert(anchor.clone()) {
                            suffix += 1;
                            anchor = format!("{base}-{suffix}");
                        }
                        result.headings.push(Heading {
                            start,
                            end: raw.len(),
                            level,
                            anchor,
                        });
                    }
                }
                Event::Start(Tag::HtmlBlock) => {
                    result.custom_html = true;
                    html_block_start = Some(body_start + range.start);
                }
                Event::End(TagEnd::HtmlBlock) => {
                    if let Some(start) = html_block_start.take() {
                        collect_html_anchors(
                            &raw[start..body_start + range.end],
                            &mut result.html_anchors,
                            &mut raw_element,
                        );
                    }
                }
                Event::Html(html) | Event::InlineHtml(html) if html_block_start.is_none() => {
                    result.custom_html = true;
                    collect_html_anchors(&html, &mut result.html_anchors, &mut raw_element);
                }
                _ => {}
            }
        }
        let mut stack: Vec<usize> = vec![];
        for index in 0..result.headings.len() {
            while let Some(&previous) = stack.last() {
                if result.headings[previous].level < result.headings[index].level {
                    break;
                }
                result.headings[previous].end = result.headings[index].start;
                stack.pop();
            }
            stack.push(index);
        }
        result
    }
    pub fn containing(&self, offset: usize) -> Option<&Heading> {
        self.headings
            .iter()
            .rev()
            .find(|h| h.start <= offset && offset < h.end)
    }
    /// Unknown custom HTML anchors and truncated heading inventories stay inconclusive.
    pub fn contains_anchor(&self, fragment: &str) -> Option<bool> {
        let decoded = percent_encoding::percent_decode_str(fragment)
            .decode_utf8()
            .ok()?;
        if decoded.is_empty() {
            return Some(true);
        }
        if self.headings.iter().any(|h| h.anchor == decoded)
            || self.html_anchors.contains(decoded.as_ref())
        {
            Some(true)
        } else if self.complete && !self.custom_html {
            Some(false)
        } else {
            None
        }
    }
}

/// Extracts `id` and `name` attribute values from raw HTML fragments, so that
/// explicit HTML anchors can be resolved for the anchor check without a full
/// HTML parser. Values are matched for quoted and unquoted forms.
fn collect_html_anchors(
    html: &str,
    anchors: &mut BTreeSet<String>,
    raw_element: &mut Option<String>,
) {
    let mut rest = html;
    loop {
        if let Some(tag) = raw_element.as_ref() {
            let closing = format!("</{tag}");
            let lower = rest.to_ascii_lowercase();
            let Some(start) = lower.match_indices(&closing).find_map(|(pos, _)| {
                rest[pos + closing.len()..]
                    .chars()
                    .next()
                    .filter(|c| c.is_ascii_whitespace() || matches!(c, '>' | '/'))
                    .map(|_| pos)
            }) else {
                return;
            };
            let Some(end) = rest[start..].find('>') else {
                return;
            };
            rest = &rest[start + end + 1..];
            *raw_element = None;
        }
        let Some(start) = rest.find('<') else { return };
        rest = &rest[start + 1..];
        // Comments, declarations and raw-text elements cannot supply anchors here.
        // Fail conservatively rather than parsing apparent attributes in their text.
        if rest.starts_with('!') || rest.starts_with('?') {
            return;
        }
        if rest.starts_with('/') {
            let Some(end) = rest.find('>') else { return };
            rest = &rest[end + 1..];
            continue;
        }
        let tag_end = rest
            .find(|c: char| c.is_ascii_whitespace() || matches!(c, '/' | '>'))
            .unwrap_or(rest.len());
        let tag = &rest[..tag_end];
        if tag.is_empty() || !tag.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-') {
            return;
        }
        let raw_text = [
            "script",
            "style",
            "textarea",
            "title",
            "xmp",
            "iframe",
            "noembed",
            "noframes",
            "plaintext",
        ]
        .iter()
        .any(|name| tag.eq_ignore_ascii_case(name));
        rest = &rest[tag_end..];
        let mut found = BTreeSet::new();
        let mut attributes = BTreeSet::new();
        loop {
            let trimmed = rest.trim_start_matches(|c: char| c.is_ascii_whitespace());
            let separated = trimmed.len() != rest.len();
            rest = trimmed;
            if let Some(tail) = rest.strip_prefix('>').or_else(|| rest.strip_prefix("/>")) {
                anchors.extend(found);
                rest = tail;
                break;
            }
            if !separated {
                return;
            }
            let end = rest
                .find(|c: char| c.is_ascii_whitespace() || matches!(c, '=' | '/' | '>'))
                .unwrap_or(rest.len());
            let name = &rest[..end];
            if name.is_empty() || name.contains(['<', '\'', '"']) {
                return;
            }
            if !attributes.insert(name.to_ascii_lowercase()) {
                return;
            }
            rest = &rest[end..];
            let after_name = rest.trim_start_matches(|c: char| c.is_ascii_whitespace());
            if let Some(after_equals) = after_name.strip_prefix('=') {
                let after_equals =
                    after_equals.trim_start_matches(|c: char| c.is_ascii_whitespace());
                let (value, tail) = read_html_attr_value(after_equals);
                let Some(tail) = tail else { return };
                if value.is_empty() && !after_equals.starts_with(['\'', '"']) {
                    return;
                }
                if !value.is_empty()
                    && (name.eq_ignore_ascii_case("id") || name.eq_ignore_ascii_case("name"))
                {
                    found.insert(decode_html_entities(&value));
                }
                rest = tail;
            }
        }
        if raw_text {
            *raw_element = Some(tag.to_ascii_lowercase());
        }
    }
}

/// Decodes the character references that can appear inside `id`/`name`
/// attribute values, so an anchor is matched against the fragment a browser
/// would expose (e.g. `id="a&amp;b"` is addressable as `#a&b`). Unknown
/// references are preserved verbatim.
fn decode_html_entities(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut rest = value;
    while let Some(pos) = rest.find('&') {
        out.push_str(&rest[..pos]);
        let tail = &rest[pos..];
        let Some(end) = tail.find(';') else {
            out.push_str(tail);
            break;
        };
        let body = &tail[1..end];
        let decoded = match body {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            "nbsp" => Some('\u{a0}'),
            _ => decode_numeric_entity(body),
        };
        match decoded {
            Some(c) => out.push(c),
            None => out.push_str(&tail[..=end]),
        }
        rest = &tail[end + 1..];
    }
    out.push_str(rest);
    out
}

fn decode_numeric_entity(body: &str) -> Option<char> {
    let (radix, digits) =
        if let Some(d) = body.strip_prefix("#x").or_else(|| body.strip_prefix("#X")) {
            (16, d)
        } else {
            let d = body.strip_prefix('#')?;
            (10, d)
        };
    let code = u32::from_str_radix(digits, radix).ok()?;
    char::from_u32(code)
}

fn read_html_attr_value(after: &str) -> (String, Option<&str>) {
    if let Some(inner) = after.strip_prefix('"') {
        match inner.find('"') {
            Some(end) => (inner[..end].to_owned(), Some(&inner[end + 1..])),
            None => (String::new(), None),
        }
    } else if let Some(inner) = after.strip_prefix('\'') {
        match inner.find('\'') {
            Some(end) => (inner[..end].to_owned(), Some(&inner[end + 1..])),
            None => (String::new(), None),
        }
    } else {
        let end = after
            .find(|c: char| c.is_whitespace() || c == '>')
            .unwrap_or(after.len());
        (after[..end].to_owned(), Some(&after[end..]))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn html_attributes_are_parsed_with_quote_and_tag_boundaries() {
        for html in [
            "<div title='id=\"ghost\"'>text</div>",
            "<div data-id='ghost'>text</div>",
            "<!-- <a id='ghost'> -->",
            "<!--\n<a id='ghost'>\n-->",
            "<script>const value = '<a id=ghost>';</script>",
            "<div title=\"<a id='ghost'>\">text</div>",
            "<div id='ghost'broken>",
            "<div id='real' id='ghost'>",
            "<script>\nconst value = '<a id=ghost>';\n</script>",
            "before <script>const value = '<a id=ghost>';</script> after",
            "<textarea>\n<a id=ghost>\n</textarea>",
        ] {
            assert_ne!(
                Sections::parse(html, 0).contains_anchor("ghost"),
                Some(true),
                "{html}"
            );
        }
        let sections =
            Sections::parse("<a TITLE='id=ghost' ID='real'></a>\n<a name=second></a>", 0);
        assert_eq!(sections.contains_anchor("real"), Some(true));
        assert_eq!(sections.contains_anchor("second"), Some(true));
        assert_ne!(sections.contains_anchor("ghost"), Some(true));
    }

    #[test]
    fn headings_preserve_boundaries_and_ignore_code() {
        let raw = "\u{feff}# ignored frontmatter\r\n# 中文\r\n## **Child** `code`\r\n```\n# fake\n```\n## Child code\ntext\n# End\n";
        let sections = Sections::parse(raw, raw.find("# 中文").unwrap());
        assert_eq!(sections.headings.len(), 4);
        assert_eq!(sections.headings[2].anchor, "child-code-1");
        let section = sections.containing(raw.find("# fake").unwrap()).unwrap();
        assert_eq!(section.anchor, "child-code");
        assert!(raw[section.start..section.end].ends_with("```\n"));
        assert_eq!(sections.contains_anchor("%E4%B8%AD%E6%96%87"), Some(true));
        assert_eq!(sections.contains_anchor("fake"), Some(false));
        assert_eq!(
            Sections::parse("# A\n<a id='custom'></a>", 0).contains_anchor("custom"),
            Some(true)
        );
        assert_eq!(
            Sections::parse("# A\n<a name='legacy'></a>", 0).contains_anchor("legacy"),
            Some(true)
        );
        assert_eq!(
            Sections::parse("# A\n<div id=\"pane\">", 0).contains_anchor("pane"),
            Some(true)
        );
        assert_eq!(
            Sections::parse("# A\n<a id='custom'></a>", 0).contains_anchor("missing"),
            None
        );
    }

    #[test]
    fn html_anchor_values_decode_character_references() {
        // Named and numeric references in `id`/`name` resolve to the fragment a
        // browser exposes, so the anchor check is deterministic instead of
        // inconclusive.
        assert_eq!(
            Sections::parse("# A\n<a id='foo&amp;bar'></a>", 0).contains_anchor("foo&bar"),
            Some(true)
        );
        assert_eq!(
            Sections::parse("# A\n<a name='a&#45;b'></a>", 0).contains_anchor("a-b"),
            Some(true)
        );
        assert_eq!(
            Sections::parse("# A\n<div id=\"x&#x2D;y\">", 0).contains_anchor("x-y"),
            Some(true)
        );
        // Unknown references stay verbatim rather than guessing.
        assert_eq!(
            Sections::parse("# A\n<a id='a&nbsp;b'></a>", 0).contains_anchor("a\u{a0}b"),
            Some(true)
        );
    }
}
