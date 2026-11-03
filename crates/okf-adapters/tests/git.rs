use okf_adapters::Git;
use okf_application::{BundleBinding, BundleReader, RequestControl, ScanBudget};
use std::{
    fs,
    path::Path,
    process::Command,
    sync::{atomic::AtomicBool, Arc},
    time::Duration,
};
fn git(root: &Path, args: &[&str]) -> String {
    let out = Command::new(std::env::var_os("OKF_GIT").unwrap_or_else(|| "git".into()))
        .arg("-C")
        .arg(root)
        .args([
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
        ])
        .args(args)
        .output()
        .expect("Git is required for Git adapter tests");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout).unwrap().trim().into()
}
fn control() -> RequestControl {
    RequestControl::new(Duration::from_secs(30), Arc::new(AtomicBool::new(false)))
}
#[test]
fn pinned_tree_ignores_worktree_and_branch_updates_and_bounds_reads() {
    let repo = tempfile::tempdir().unwrap();
    git(repo.path(), &["init", "--initial-branch=main"]);
    fs::create_dir(repo.path().join("knowledge")).unwrap();
    let raw = "---\ntype: Note\n---\n认证🙂\n";
    fs::write(repo.path().join("knowledge/认证.md"), raw).unwrap();
    fs::write(repo.path().join("knowledge/run.py"), b"do not execute").unwrap();
    git(repo.path(), &["add", "."]);
    git(repo.path(), &["commit", "-m", "first"]);
    let pinned = Git::pin(repo.path(), "main", "knowledge", &control()).unwrap();
    let b = BundleBinding {
        id: "git-book".into(),
        name: "book".into(),
        kind: "git".into(),
        root: repo.path().to_string_lossy().into_owned(),
        scope: "project".into(),
        git: Some(pinned.clone()),
    };
    fs::write(
        repo.path().join("knowledge/认证.md"),
        "---\ntype: Note\n---\nnew",
    )
    .unwrap();
    git(repo.path(), &["add", "."]);
    git(repo.path(), &["commit", "-m", "second"]);
    assert_eq!(Git.read(&b, "认证.md", 1024, &control()).unwrap(), raw);
    assert_ne!(
        Git::pin(repo.path(), "main", "knowledge", &control())
            .unwrap()
            .commit,
        pinned.commit
    );
    assert_eq!(
        Git.read(&b, "认证.md", 1, &control()).unwrap_err().code,
        "DOCUMENT_TOO_LARGE"
    );
    assert!(Git.stat(&b, "run.py", &control()).unwrap().is_some());
    assert!(Git.stat(&b, "missing.py", &control()).unwrap().is_none());
    let c = control();
    let listing = Git.list(&b, "", &mut ScanBudget::new(100, &c)).unwrap();
    assert!(listing.complete);
    assert_eq!(listing.entries.len(), 1);
    assert_eq!(listing.entries[0].path, "认证.md");
    assert!(
        !Git.list(&b, "", &mut ScanBudget::new(1, &c))
            .unwrap()
            .complete
    );
    assert_eq!(
        Git.read(&b, "../secret.md", 1024, &c).unwrap_err().code,
        "INVALID_PATH"
    );
    assert!(Git::pin(repo.path(), "--help", "", &c).is_err());
}

#[test]
fn git_symlink_objects_are_not_followed_even_without_worktree_links() {
    let repo = tempfile::tempdir().unwrap();
    git(repo.path(), &["init"]);
    fs::write(repo.path().join("target.md"), "---\ntype: Note\n---\n").unwrap();
    git(repo.path(), &["add", "target.md"]);
    let blob = git(repo.path(), &["hash-object", "-w", "target.md"]);
    git(
        repo.path(),
        &[
            "update-index",
            "--add",
            "--cacheinfo",
            &format!("120000,{blob},link.md"),
        ],
    );
    git(repo.path(), &["commit", "-m", "symlink"]);
    let b = BundleBinding {
        id: "book".into(),
        name: "book".into(),
        kind: "git".into(),
        root: repo.path().to_string_lossy().into_owned(),
        scope: "project".into(),
        git: Some(Git::pin(repo.path(), "HEAD", "", &control()).unwrap()),
    };
    assert_eq!(
        Git.read(&b, "link.md", 1024, &control()).unwrap_err().code,
        "GIT_UNSUPPORTED_ENTRY"
    );
}

#[test]
fn cache_compaction_keeps_pinned_objects_and_serializes_writers() {
    let source = tempfile::tempdir().unwrap();
    git(source.path(), &["init", "--initial-branch=main"]);
    let raw = "---\ntype: Note\n---\nold commit\n";
    fs::write(source.path().join("note.md"), raw).unwrap();
    git(source.path(), &["add", "."]);
    git(source.path(), &["commit", "-m", "old"]);
    let old = git(source.path(), &["rev-parse", "HEAD"]);
    fs::write(source.path().join("note.md"), "---\ntype: Note\n---\nnew").unwrap();
    git(source.path(), &["add", "."]);
    git(source.path(), &["commit", "-m", "new"]);
    let bare = tempfile::tempdir().unwrap();
    git(
        source.path(),
        &["clone", "--bare", ".", bare.path().to_str().unwrap()],
    );
    let binding = BundleBinding {
        id: "cache".into(),
        name: "cache".into(),
        kind: "git".into(),
        root: bare.path().to_string_lossy().into_owned(),
        scope: "project".into(),
        git: Some(Git::pin(bare.path(), &old, "", &control()).unwrap()),
    };
    let lock = Git::lock(bare.path()).unwrap();
    assert_eq!(
        Git::cache(bare.path(), true, &control()).unwrap_err().code,
        "GIT_CACHE_LOCKED"
    );
    drop(lock);
    let stats = Git::cache(bare.path(), true, &control()).unwrap();
    assert_eq!(stats["compacted"], true);
    assert_eq!(
        Git.read(&binding, "note.md", 1024, &control()).unwrap(),
        raw
    );
    assert_eq!(
        Git::fetch_bounded(
            bare.path(),
            "https://localhost:1/unreachable",
            "main",
            None,
            &control(),
            1,
            None
        )
        .unwrap_err()
        .code,
        "GIT_STORAGE_LIMIT"
    );
}

#[test]
fn remote_url_validation_accepts_ssh_and_rejects_embedded_credentials() {
    let source = tempfile::tempdir().unwrap();
    git(source.path(), &["init", "--initial-branch=main"]);
    fs::write(
        source.path().join("note.md"),
        "---\ntype: Note\n---\nbody\n",
    )
    .unwrap();
    git(source.path(), &["add", "."]);
    git(source.path(), &["commit", "-m", "first"]);
    let bare = tempfile::tempdir().unwrap();
    git(
        source.path(),
        &["clone", "--bare", ".", bare.path().to_str().unwrap()],
    );
    // Valid HTTPS and SSH remotes pass URL validation and fail later on the
    // storage limit (never with INVALID_REMOTE).
    for remote in [
        "https://github.com/owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
    ] {
        let err =
            Git::fetch_bounded(bare.path(), remote, "main", None, &control(), 1, None).unwrap_err();
        assert_ne!(err.code, "INVALID_REMOTE", "{remote}: {err:?}");
    }
    // Embedded credentials, non-HTTPS/SSH schemes and control characters are
    // rejected before any repository work.
    for remote in [
        "https://user:token@github.com/owner/repo.git",
        "file:///tmp/repo",
        "C:\\path\\repo",
        "git@github.com:owner/repo.git\nmalicious",
    ] {
        let err =
            Git::fetch_bounded(bare.path(), remote, "main", None, &control(), 1, None).unwrap_err();
        assert_eq!(err.code, "INVALID_REMOTE", "{remote}");
    }
}

fn git_lfs_available() -> bool {
    Command::new(std::env::var_os("OKF_GIT").unwrap_or_else(|| "git".into()))
        .args(["lfs", "version"])
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
}

/// Convert a local path into a `file://` URL accepted by `git lfs fetch` on
/// every platform. Unix paths already begin with `/`; Windows drive-letter
/// paths (e.g. `C:\tmp`) need forward slashes and a leading slash.
fn file_url(path: &Path) -> String {
    let mut s = path.to_string_lossy().replace('\\', "/");
    if !s.starts_with('/') {
        s.insert(0, '/');
    }
    format!("file://{s}")
}

fn count_lfs_objects(root: &Path) -> usize {
    let base = root.join("lfs").join("objects");
    if !base.is_dir() {
        return 0;
    }
    let mut count = 0;
    let mut stack = vec![base];
    while let Some(dir) = stack.pop() {
        if let Ok(entries) = fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    stack.push(path);
                } else {
                    count += 1;
                }
            }
        }
    }
    count
}

#[test]
fn fetch_lfs_downloads_objects_for_the_fetched_ref() {
    if !git_lfs_available() {
        eprintln!("skipping: git-lfs not installed");
        return;
    }
    let source = tempfile::tempdir().unwrap();
    git(source.path(), &["init", "--initial-branch=main"]);
    git(source.path(), &["lfs", "install", "--local"]);
    git(source.path(), &["lfs", "track", "*.bin"]);
    let content = vec![b'x'; 50_000];
    fs::write(source.path().join("big.bin"), &content).unwrap();
    git(source.path(), &["add", ".gitattributes", "big.bin"]);
    git(source.path(), &["commit", "-m", "lfs"]);
    // A bare "server" holds the LFS objects, simulating the LFS endpoint.
    let server = tempfile::tempdir().unwrap();
    let server_path = server.path().to_string_lossy().into_owned();
    git(server.path(), &["init", "--bare"]);
    git(source.path(), &["remote", "add", "origin", &server_path]);
    git(source.path(), &["push", "-q", "origin", "main"]);
    git(source.path(), &["lfs", "push", "origin", "main"]);
    assert_eq!(count_lfs_objects(server.path()), 1);
    // A destination bare repository fetches the commit but not the LFS objects.
    let dest = tempfile::tempdir().unwrap();
    git(dest.path(), &["init", "--bare"]);
    git(
        dest.path(),
        &["fetch", "-q", &server_path, "main:refs/heads/main"],
    );
    assert_eq!(count_lfs_objects(dest.path()), 0);
    // fetch_lfs downloads the LFS object into the destination cache.
    Git::fetch_lfs(
        dest.path(),
        &file_url(server.path()),
        "refs/heads/main",
        &control(),
        u64::MAX,
    );
    assert_eq!(count_lfs_objects(dest.path()), 1);
}

#[test]
fn lfs_pointer_reads_real_content_when_cached_and_reports_when_missing() {
    if !git_lfs_available() {
        eprintln!("skipping: git-lfs not installed");
        return;
    }
    let repo = tempfile::tempdir().unwrap();
    git(repo.path(), &["init", "--initial-branch=main"]);
    git(repo.path(), &["lfs", "install", "--local"]);
    git(repo.path(), &["lfs", "track", "*.bin"]);
    let content = "---\ntype: Note\n---\nLFS body\n".repeat(1000);
    fs::write(repo.path().join("doc.bin"), &content).unwrap();
    git(repo.path(), &["add", ".gitattributes", "doc.bin"]);
    git(repo.path(), &["commit", "-m", "lfs"]);
    // The committed blob is now a placeholder pointer, not the content.
    let pointer = git(repo.path(), &["cat-file", "blob", "HEAD:doc.bin"]);
    assert!(pointer.starts_with("version https://git-lfs.github.com/spec/v1"));
    let b = BundleBinding {
        id: "lfs".into(),
        name: "lfs".into(),
        kind: "git".into(),
        root: repo.path().to_string_lossy().into_owned(),
        scope: "project".into(),
        git: Some(Git::pin(repo.path(), "HEAD", "", &control()).unwrap()),
    };
    // Cached object -> real content is returned, not the pointer.
    assert_eq!(
        Git.read(&b, "doc.bin", content.len() + 1, &control())
            .unwrap(),
        content
    );
    // Missing object -> clear signal, never the pointer text.
    let objects = repo.path().join(".git/lfs/objects");
    let backup = repo.path().join("lfs-objects-backup");
    fs::rename(&objects, &backup).unwrap();
    assert_eq!(
        Git.read(&b, "doc.bin", content.len() + 1, &control())
            .unwrap_err()
            .code,
        "LFS_CONTENT_UNAVAILABLE"
    );
    fs::rename(&backup, &objects).unwrap();
}

#[test]
fn submodule_content_resolves_once_objects_are_fetched() {
    // Submodule repository.
    let sub = tempfile::tempdir().unwrap();
    git(sub.path(), &["init", "--initial-branch=main"]);
    let raw = "---\ntype: Note\n---\nsubmodule body\n";
    fs::write(sub.path().join("README.md"), raw).unwrap();
    git(sub.path(), &["add", "README.md"]);
    git(sub.path(), &["commit", "-m", "sub"]);
    let sub_oid = git(sub.path(), &["rev-parse", "HEAD"]);

    // Superproject with the submodule at `subdir`.
    let sup = tempfile::tempdir().unwrap();
    git(sup.path(), &["init", "--initial-branch=main"]);
    git(
        sup.path(),
        &[
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            sub.path().to_str().unwrap(),
            "subdir",
        ],
    );
    git(sup.path(), &["commit", "-m", "add submodule"]);

    let binding = BundleBinding {
        id: "sm".into(),
        name: "sm".into(),
        kind: "git".into(),
        root: sup.path().to_string_lossy().into_owned(),
        scope: "project".into(),
        git: Some(Git::pin(sup.path(), "HEAD", "", &control()).unwrap()),
    };

    // Without the submodule objects, the gitlink cannot resolve.
    assert_eq!(
        Git.read(&binding, "subdir/README.md", 1024, &control())
            .unwrap_err()
            .code,
        "GIT_OBJECT_UNAVAILABLE"
    );

    // Fetch the submodule objects into the superproject store (what
    // `fetch_submodules` does) and the content resolves through the gitlink.
    git(
        sup.path(),
        &[
            "-c",
            "protocol.file.allow=always",
            "fetch",
            "-q",
            sub.path().to_str().unwrap(),
            &sub_oid,
        ],
    );
    assert_eq!(
        Git.read(&binding, "subdir/README.md", 1024, &control())
            .unwrap(),
        raw
    );

    // The submodule shows up as a directory in listings.
    let c = control();
    let listing = Git
        .list(&binding, "", &mut ScanBudget::new(100, &c))
        .unwrap();
    assert!(listing
        .entries
        .iter()
        .any(|e| e.path == "subdir" && e.directory));
}
