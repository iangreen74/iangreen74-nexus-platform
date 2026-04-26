"""Tests for the four Phase 0a (Track C) codebase-indexing read tools.

Each tool is exercised against a mocked GitHub API. The patterns mirror
test_overwatch_v2_read_tools.TestGitHub: the installation-token mint is
patched per-test, and httpx.Client is replaced with a context-manager
MagicMock so we can drive responses without hitting the network.

Coverage per spec:
- Each tool's happy path
- Repo not in allowlist → ToolForbidden
- File not found → ToolNotFound
- Large file (>1MB) → truncated with warning
- Largest file (>10MB) → outright refusal
- Invalid ref → ToolNotFound (GitHub returns 404 for unresolvable refs)
- Search with no results → empty list, ok envelope intact
- Search results pagination / truncation flag
"""
from __future__ import annotations

import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.overwatch_v2.tools.read_tools import (  # noqa: E402
    list_repo_files, read_git_diff, read_repo_file, search_codebase,
)
from nexus.overwatch_v2.tools.read_tools._repo_allowlist import (  # noqa: E402
    ALLOWED_REPOS, assert_repo_allowed,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (  # noqa: E402
    ToolForbidden, ToolNotFound, ToolThrottled, ToolUnknown,
)


REPO_OK = "iangreen74/iangreen74-nexus-platform"
REPO_OK_2 = "iangreen74/aria-platform"
REPO_BAD = "evil/repo"


# --- Helpers ----------------------------------------------------------------


def _gh_resp(status: int = 200, body=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text or (json.dumps(body) if body is not None else "")
    r.json.return_value = body if body is not None else {}
    return r


def _patch_token_for(module):
    """Stub get_installation_token() in the given tool module."""
    return patch.object(module, "get_installation_token", return_value="tok-test")


# === TestRepoAllowlist =====================================================


class TestRepoAllowlist:
    def test_allowed_repos_is_frozenset(self):
        assert isinstance(ALLOWED_REPOS, frozenset)
        assert REPO_OK in ALLOWED_REPOS
        assert REPO_OK_2 in ALLOWED_REPOS

    def test_assert_repo_allowed_passes_for_allowed(self):
        # Should not raise.
        assert_repo_allowed(REPO_OK)
        assert_repo_allowed(REPO_OK_2)

    def test_assert_repo_allowed_rejects_unknown(self):
        with pytest.raises(ToolForbidden) as excinfo:
            assert_repo_allowed(REPO_BAD)
        assert REPO_BAD in str(excinfo.value)
        assert "allowlist" in str(excinfo.value).lower()


# === TestReadRepoFile ======================================================


class TestReadRepoFile:
    def setup_method(self):
        self._tok = _patch_token_for(read_repo_file)
        self._tok.start()

    def teardown_method(self):
        self._tok.stop()

    def _file_body(self, content: bytes = b"hello world\n", **overrides):
        body = {
            "name": "README.md", "path": "README.md", "sha": "abc123",
            "size": len(content),
            "encoding": "base64",
            "content": base64.b64encode(content).decode(),
            "type": "file",
        }
        body.update(overrides)
        return body

    def _commits_body(self):
        return [{
            "sha": "deadbeef",
            "commit": {
                "author": {"name": "Ian", "date": "2026-04-23T14:23:11Z"},
                "message": "msg",
            },
            "author": {"login": "iangreen74"},
        }]

    def test_happy_path_returns_decoded_content_and_metadata(self):
        py_body = self._file_body(b"def main():\n    pass\n",
                                   name="main.py", path="nexus/main.py")
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.side_effect = [
                _gh_resp(200, body=py_body),
                _gh_resp(200, body=self._commits_body()),
            ]
            r = read_repo_file.handler(
                repo=REPO_OK, path="nexus/main.py", ref="main",
            )
        assert r["content"] == "def main():\n    pass\n"
        assert r["sha"] == "abc123"
        assert r["size_bytes"] == len(b"def main():\n    pass\n")
        assert r["language"] == "python"
        assert r["last_modified"] == "2026-04-23T14:23:11Z"
        assert r["last_modified_by"] == "iangreen74"
        assert r["repo"] == REPO_OK
        assert r["truncated"] is False
        assert r["warning"] is None
        assert r["lines"] == 2

    def test_repo_not_in_allowlist_raises_forbidden(self):
        with pytest.raises(ToolForbidden):
            read_repo_file.handler(repo=REPO_BAD, path="x")

    def test_file_not_found(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="missing")
            with pytest.raises(ToolNotFound):
                read_repo_file.handler(repo=REPO_OK, path="nope.py")

    def test_invalid_ref_returns_not_found(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="ref not found")
            with pytest.raises(ToolNotFound):
                read_repo_file.handler(repo=REPO_OK, path="x.py",
                                       ref="not-a-real-branch")

    def test_large_file_over_1mb_truncated_with_warning(self):
        body = self._file_body()
        body["size"] = 2 * 1024 * 1024  # 2 MB
        body["content"] = ""             # GitHub strips content above 1 MB
        body["encoding"] = "none"
        body["truncated"] = True
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.side_effect = [
                _gh_resp(200, body=body),
                _gh_resp(200, body=self._commits_body()),
            ]
            r = read_repo_file.handler(repo=REPO_OK, path="big.bin")
        assert r["truncated"] is True
        assert r["warning"] is not None
        assert "1048576" in r["warning"] or "truncated" in r["warning"].lower()
        assert r["size_bytes"] == 2 * 1024 * 1024
        assert r["content"] == ""

    def test_huge_file_over_10mb_refused(self):
        body = self._file_body()
        body["size"] = 20 * 1024 * 1024  # 20 MB
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=body)
            with pytest.raises(ToolUnknown) as excinfo:
                read_repo_file.handler(repo=REPO_OK, path="huge.bin")
        assert "too large" in str(excinfo.value)

    def test_directory_path_raises_unknown(self):
        # contents API returns a list when path is a dir.
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=[
                {"name": "a.py", "type": "file"},
            ])
            with pytest.raises(ToolUnknown):
                read_repo_file.handler(repo=REPO_OK, path="nexus/")

    def test_last_modified_best_effort_when_commits_call_fails(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.side_effect = [
                _gh_resp(200, body=self._file_body()),
                _gh_resp(500, text="boom"),
            ]
            r = read_repo_file.handler(repo=REPO_OK, path="README.md")
        # Main read still succeeds; metadata fields gracefully None.
        assert r["sha"] == "abc123"
        assert r["last_modified"] is None
        assert r["last_modified_by"] is None

    def test_403_forbidden_propagates(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(403, text="rate limit")
            with pytest.raises(ToolForbidden):
                read_repo_file.handler(repo=REPO_OK, path="x.py")

    def test_429_throttled_propagates(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(429, text="slow down")
            with pytest.raises(ToolThrottled):
                read_repo_file.handler(repo=REPO_OK, path="x.py")

    def test_default_ref_is_main(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.side_effect = [
                _gh_resp(200, body=self._file_body()),
                _gh_resp(200, body=self._commits_body()),
            ]
            r = read_repo_file.handler(repo=REPO_OK, path="README.md")
        assert r["ref"] == "main"
        first_call = ctx.get.call_args_list[0]
        assert first_call.kwargs["params"]["ref"] == "main"

    def test_language_inference(self):
        cases = [
            ("a.py", "python"), ("a.ts", "typescript"), ("a.go", "go"),
            ("a.yml", "yaml"), ("a.unknownext", "unknown"),
        ]
        for path, expected_lang in cases:
            with patch("httpx.Client") as cls:
                ctx = cls.return_value.__enter__.return_value
                body = self._file_body()
                body["path"] = path
                body["name"] = path
                ctx.get.side_effect = [
                    _gh_resp(200, body=body),
                    _gh_resp(200, body=[]),
                ]
                r = read_repo_file.handler(repo=REPO_OK, path=path)
            assert r["language"] == expected_lang, f"{path} → {r['language']}"


# === TestSearchCodebase ====================================================


class TestSearchCodebase:
    def setup_method(self):
        self._tok = _patch_token_for(search_codebase)
        self._tok.start()

    def teardown_method(self):
        self._tok.stop()

    def _result(self, name="graph_backend.py", path="nexus/graph_backend.py",
                fragment="from nexus.graph_backend import query"):
        return {
            "name": name, "path": path, "sha": "x", "score": 1.0,
            "html_url": f"https://github.com/example/{path}",
            "text_matches": [{"fragment": fragment,
                              "matches": [{"text": "graph_backend",
                                           "indices": [5, 18]}]}],
        }

    def test_happy_path_with_repo_filter(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 23,
                "items": [self._result()],
            })
            r = search_codebase.handler(query="graph_backend", repo=REPO_OK)
        assert len(r["results"]) == 1
        result = r["results"][0]
        assert result["repo"] == REPO_OK
        assert result["path"] == "nexus/graph_backend.py"
        assert result["match_type"] in ("filename", "content")
        assert result["context"].startswith("from nexus.graph_backend")
        assert r["total_found"] == 23
        assert r["repos_searched"] == [REPO_OK]

    def test_no_repo_searches_all_allowlisted(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 0, "items": [],
            })
            r = search_codebase.handler(query="zzz_no_match")
        assert sorted(r["repos_searched"]) == sorted(ALLOWED_REPOS)
        # one HTTP call per allowlisted repo
        assert ctx.get.call_count == len(ALLOWED_REPOS)

    def test_empty_results_returns_empty_array(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 0, "items": [],
            })
            r = search_codebase.handler(query="zzz", repo=REPO_OK)
        assert r["results"] == []
        assert r["total_found"] == 0
        assert r["truncated"] is False

    def test_repo_not_in_allowlist_raises_forbidden(self):
        with pytest.raises(ToolForbidden):
            search_codebase.handler(query="x", repo=REPO_BAD)

    def test_match_type_filename_when_query_in_path(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 1,
                "items": [self._result(name="graph_backend.py",
                                       path="nexus/graph_backend.py",
                                       fragment="unrelated text")],
            })
            r = search_codebase.handler(query="graph_backend", repo=REPO_OK)
        assert r["results"][0]["match_type"] == "filename"

    def test_match_type_content_when_query_only_in_body(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 1,
                "items": [self._result(name="other.py",
                                       path="nexus/other.py",
                                       fragment="from x import CrossTenant")],
            })
            r = search_codebase.handler(query="CrossTenant", repo=REPO_OK)
        assert r["results"][0]["match_type"] == "content"

    def test_results_truncated_when_total_exceeds_returned(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 200,
                "items": [self._result()],
            })
            r = search_codebase.handler(query="x", repo=REPO_OK,
                                        max_results=20)
        assert r["truncated"] is True
        assert r["total_found"] == 200

    def test_max_results_clamped_to_hard_max(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 0, "items": [],
            })
            search_codebase.handler(query="x", repo=REPO_OK, max_results=999)
        params = ctx.get.call_args.kwargs["params"]
        # Single-repo path uses max_results directly; clamped to 100.
        assert params["per_page"] == 100

    def test_empty_query_raises(self):
        with pytest.raises(ToolUnknown):
            search_codebase.handler(query="   ")

    def test_422_invalid_query(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(422, text="bad q syntax")
            with pytest.raises(ToolUnknown):
                search_codebase.handler(query="??invalid??", repo=REPO_OK)

    def test_429_throttled(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(429, text="slow down")
            with pytest.raises(ToolThrottled):
                search_codebase.handler(query="x", repo=REPO_OK)

    def test_query_includes_repo_qualifier(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "total_count": 0, "items": [],
            })
            search_codebase.handler(query="needle", repo=REPO_OK)
        sent_q = ctx.get.call_args.kwargs["params"]["q"]
        assert "needle" in sent_q
        assert f"repo:{REPO_OK}" in sent_q


# === TestReadGitDiff =======================================================


class TestReadGitDiff:
    def setup_method(self):
        self._tok = _patch_token_for(read_git_diff)
        self._tok.start()

    def teardown_method(self):
        self._tok.stop()

    def _commit_body(self, files=None):
        return {
            "sha": "abc123",
            "html_url": "https://github.com/x/y/commit/abc123",
            "commit": {
                "message": "fix(routing): redirect root / to /engineering",
                "author": {"name": "Ian Green", "date": "2026-04-25T10:00:00Z"},
            },
            "stats": {"additions": 12, "deletions": 3, "total": 15},
            "files": files if files is not None else [{
                "filename": "nexus/server.py",
                "status": "modified",
                "additions": 12, "deletions": 3, "changes": 15,
                "patch": "@@ -45,7 +45,16 @@ def index():\n+    return RedirectResponse(...)",
            }],
        }

    def test_happy_path(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._commit_body())
            r = read_git_diff.handler(repo=REPO_OK_2, sha="abc123")
        assert r["sha"] == "abc123"
        assert r["files_changed"] == 1
        assert r["additions"] == 12
        assert r["deletions"] == 3
        assert r["diffs"][0]["path"] == "nexus/server.py"
        assert r["diffs"][0]["patch"].startswith("@@ -45")
        assert r["diffs"][0]["patch_truncated"] is False
        assert r["filtered_to_file"] is None

    def test_file_filter_returns_only_matching_path(self):
        files = [
            {"filename": "a.py", "status": "modified",
             "additions": 1, "deletions": 0, "patch": "patch-a"},
            {"filename": "b.py", "status": "modified",
             "additions": 2, "deletions": 1, "patch": "patch-b"},
        ]
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._commit_body(files))
            r = read_git_diff.handler(repo=REPO_OK_2, sha="abc",
                                      file="b.py")
        assert r["files_changed"] == 1
        assert r["diffs"][0]["path"] == "b.py"
        assert r["filtered_to_file"] == "b.py"

    def test_file_filter_no_match_returns_empty_diffs(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._commit_body())
            r = read_git_diff.handler(repo=REPO_OK_2, sha="abc",
                                      file="not-touched.py")
        assert r["files_changed"] == 0
        assert r["diffs"] == []

    def test_repo_not_in_allowlist_raises_forbidden(self):
        with pytest.raises(ToolForbidden):
            read_git_diff.handler(repo=REPO_BAD, sha="abc")

    def test_sha_not_found(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="no such sha")
            with pytest.raises(ToolNotFound):
                read_git_diff.handler(repo=REPO_OK_2, sha="deadbeef")

    def test_huge_patch_truncated_per_file(self):
        big = "+" + ("X" * 60_000)  # 60 KB > 50 KB cap
        files = [{
            "filename": "f.py", "status": "modified",
            "additions": 60000, "deletions": 0, "patch": big,
        }]
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._commit_body(files))
            r = read_git_diff.handler(repo=REPO_OK_2, sha="abc")
        assert r["diffs"][0]["patch_truncated"] is True
        assert len(r["diffs"][0]["patch"]) == 50_000

    def test_403_forbidden(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(403, text="auth fail")
            with pytest.raises(ToolForbidden):
                read_git_diff.handler(repo=REPO_OK_2, sha="x")


# === TestListRepoFiles =====================================================


class TestListRepoFiles:
    def setup_method(self):
        self._tok = _patch_token_for(list_repo_files)
        self._tok.start()

    def teardown_method(self):
        self._tok.stop()

    def _dir_body(self):
        return [
            {"name": "graph_backend.py", "path": "nexus/graph_backend.py",
             "type": "file", "size": 5421, "sha": "f1"},
            {"name": "aria", "path": "nexus/aria",
             "type": "dir", "size": 0, "sha": "d1"},
            {"name": "server.py", "path": "nexus/server.py",
             "type": "file", "size": 9821, "sha": "f2"},
        ]

    def test_happy_path(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._dir_body())
            r = list_repo_files.handler(repo=REPO_OK, path="nexus/")
        assert r["entry_count"] == 3
        # Dirs come first after sort.
        assert r["entries"][0]["type"] == "dir"
        assert r["entries"][0]["name"] == "aria"
        assert r["repo"] == REPO_OK

    def test_root_listing_when_path_empty(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._dir_body())
            r = list_repo_files.handler(repo=REPO_OK)
        assert r["path"] == ""
        # URL must point at /contents/ (trailing slash) for root listing.
        assert ctx.get.call_args.args[0].endswith("/contents/")

    def test_repo_not_in_allowlist_raises_forbidden(self):
        with pytest.raises(ToolForbidden):
            list_repo_files.handler(repo=REPO_BAD)

    def test_path_to_file_raises_unknown(self):
        # contents API returns a single dict for files.
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body={
                "name": "x.py", "type": "file", "path": "x.py",
            })
            with pytest.raises(ToolUnknown):
                list_repo_files.handler(repo=REPO_OK, path="x.py")

    def test_path_not_found(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="missing")
            with pytest.raises(ToolNotFound):
                list_repo_files.handler(repo=REPO_OK, path="ghost/")

    def test_invalid_ref(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(404, text="no such ref")
            with pytest.raises(ToolNotFound):
                list_repo_files.handler(repo=REPO_OK, path="nexus/",
                                        ref="not-a-real-branch")

    def test_default_ref_is_main(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(200, body=self._dir_body())
            r = list_repo_files.handler(repo=REPO_OK, path="nexus/")
        assert r["ref"] == "main"
        params = ctx.get.call_args.kwargs["params"]
        assert params["ref"] == "main"

    def test_429_throttled(self):
        with patch("httpx.Client") as cls:
            ctx = cls.return_value.__enter__.return_value
            ctx.get.return_value = _gh_resp(429, text="slow down")
            with pytest.raises(ToolThrottled):
                list_repo_files.handler(repo=REPO_OK)


# === TestRegistration ======================================================


def _fake_registry_module():
    mod = MagicMock()
    mod.RISK_LOW = "low"
    mod.RISK_MEDIUM = "medium"
    mod.RISK_HIGH = "high"

    class _ToolSpec:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    mod.ToolSpec = _ToolSpec
    mod.register = MagicMock()
    mod.list_tools = MagicMock(return_value=[])
    return mod


class TestRegistration:
    def test_each_tool_registers_as_low_risk_no_approval(self):
        import sys
        for module in (read_repo_file, search_codebase,
                       read_git_diff, list_repo_files):
            fake = _fake_registry_module()
            with patch.dict(sys.modules,
                            {"nexus.overwatch_v2.tools.registry": fake}):
                module.register_tool()
            assert fake.register.call_count == 1
            spec = fake.register.call_args.args[0]
            assert spec.requires_approval is False
            assert spec.risk_level == "low"

    def test_register_all_includes_phase_0a_tools(self):
        import sys
        fake = _fake_registry_module()
        with patch.dict(sys.modules,
                        {"nexus.overwatch_v2.tools.registry": fake}):
            from nexus.overwatch_v2.tools.read_tools._registration import (
                register_all_read_tools,
            )
            register_all_read_tools()
        names = {c.args[0].name for c in fake.register.call_args_list}
        for expected in ("read_repo_file", "search_codebase",
                         "read_git_diff", "list_repo_files"):
            assert expected in names, f"{expected} not registered"
        # 7 (existing) + 4 (Phase 0a) + 4 (Phase 1) + 4 (Phase 0b) +
        # 1 (Echo Phase 1: comment_on_pr) = 20.
        assert len(names) == 20
