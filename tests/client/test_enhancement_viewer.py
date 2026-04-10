# tests/client/test_enhancement_viewer.py
"""Tests for enhancement_viewer HTTP server and HTML generation."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from mega_code.client.enhancement_viewer import (
    ReviewHandler,
    generate_review_html,
)


def _sample_eval_data() -> dict:
    """Build minimal eval data for testing."""
    return {
        "skill_name": "test-skill",
        "model": "claude-opus-4-6",
        "test_cases": [
            {
                "task": "Write a function to check if a number is prime",
                "expectations": [
                    {"text": "Function handles edge cases like 0 and 1"},
                    {"text": "Function uses efficient trial division"},
                ],
            },
            {
                "task": "Create a REST API endpoint for user registration",
                "expectations": [
                    {"text": "Validates email format"},
                    {"text": "Hashes password before storage"},
                    {"text": "Returns 201 status code on success"},
                ],
            },
        ],
        "ab_outputs": [
            {
                "task": "Write a function to check if a number is prime",
                "with_skill_output": "def is_prime(n):\n    if n <= 1: return False\n    ...",
                "baseline_output": "def check_prime(n):\n    for i in range(2, n): ...",
                "with_skill_tokens": 150,
                "baseline_tokens": 200,
            },
            {
                "task": "Create a REST API endpoint for user registration",
                "with_skill_output": "@app.post('/register', status_code=201)...",
                "baseline_output": "@app.post('/register')...",
                "with_skill_tokens": 300,
                "baseline_tokens": 350,
            },
        ],
        "gradings": [
            {
                "with_skill_gradings": [
                    {
                        "expectation": "Function handles edge cases",
                        "passed": True,
                        "evidence": "Returns False for 0 and 1",
                    },
                    {
                        "expectation": "Function uses efficient trial division",
                        "passed": True,
                        "evidence": "Loops up to sqrt(n)",
                    },
                ],
                "baseline_gradings": [
                    {
                        "expectation": "Function handles edge cases",
                        "passed": False,
                        "evidence": "No check for n <= 1",
                    },
                    {
                        "expectation": "Function uses efficient trial division",
                        "passed": False,
                        "evidence": "Loops up to n",
                    },
                ],
            },
            {
                "with_skill_gradings": [
                    {
                        "expectation": "Validates email format",
                        "passed": True,
                        "evidence": "Uses regex validation",
                    },
                    {
                        "expectation": "Hashes password before storage",
                        "passed": True,
                        "evidence": "Uses bcrypt",
                    },
                    {
                        "expectation": "Returns 201 status code",
                        "passed": True,
                        "evidence": "status_code=201",
                    },
                ],
                "baseline_gradings": [
                    {
                        "expectation": "Validates email format",
                        "passed": False,
                        "evidence": "No validation",
                    },
                    {
                        "expectation": "Hashes password before storage",
                        "passed": True,
                        "evidence": "Uses hashlib",
                    },
                    {
                        "expectation": "Returns 201 status code",
                        "passed": False,
                        "evidence": "Returns 200",
                    },
                ],
            },
        ],
    }


# =============================================================================
# generate_review_html (pure function, no server needed)
# =============================================================================


class TestGenerateReviewHtml:
    def test_produces_valid_html(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_embeds_skill_name(self):
        html = generate_review_html(_sample_eval_data(), "my-cool-skill", 1)
        assert "my-cool-skill" in html

    def test_embeds_iteration(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 3)
        assert '"iteration": 3' in html or '"iteration":3' in html

    def test_embeds_test_cases(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "Write a function to check if a number is prime" in html
        assert "REST API endpoint" in html

    def test_embeds_gradings(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "Function handles edge cases" in html
        assert "Returns False for 0 and 1" in html

    def test_embeds_ab_outputs(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "is_prime" in html
        assert "check_prime" in html

    def test_contains_feedback_elements(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "feedback-textarea" in html
        assert "Submit" in html

    def test_submit_starts_disabled_until_all_cases_are_visited(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert 'id="submit-btn"' in html
        assert 'id="submit-btn" onclick="submitFeedback()" disabled' in html
        assert "visitedCases" in html
        assert "canSubmit" in html

    def test_contains_tab_structure(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "panel-outputs" in html
        assert "panel-benchmark" in html

    def test_contains_navigation(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "prev-btn" in html
        assert "next-btn" in html

    def test_no_previous_data(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1, previous_data=None)
        assert '"previous"' not in html or "null" in html

    def test_with_previous_data(self):
        prev = {
            "ab_outputs": [
                {"with_skill_output": "old output 1"},
                {"with_skill_output": "old output 2"},
            ],
            "feedback": {0: "good job", 1: "needs work"},
        }
        html = generate_review_html(_sample_eval_data(), "test-skill", 2, previous_data=prev)
        assert "old output 1" in html
        assert "good job" in html

    def test_empty_eval_data(self):
        empty = {"test_cases": [], "ab_outputs": [], "gradings": []}
        html = generate_review_html(empty, "test-skill", 1)
        assert "<!DOCTYPE html>" in html

    def test_embedded_data_is_valid_json(self):
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        marker = "const EMBEDDED_DATA = "
        start = html.index(marker) + len(marker)
        end = html.index(";", start)
        json_str = html[start:end]
        data = json.loads(json_str)
        assert data["skill_name"] == "test-skill"
        assert data["iteration"] == 1
        assert len(data["test_cases"]) == 2

    def test_submit_posts_to_api(self):
        """HTML should POST feedback to /api/feedback."""
        html = generate_review_html(_sample_eval_data(), "test-skill", 1)
        assert "fetch('/api/feedback'" in html


# =============================================================================
# HTTP Server
# =============================================================================


class TestReviewServer:
    def _start_server(self, tmp_path):
        """Start server in a thread, return (server, port)."""
        from functools import partial
        from http.server import HTTPServer

        eval_data_path = tmp_path / "eval-full.json"
        eval_data_path.write_text(json.dumps(_sample_eval_data()), encoding="utf-8")
        feedback_path = tmp_path / "feedback.json"

        handler = partial(
            ReviewHandler,
            eval_data_path,
            "test-skill",
            1,
            feedback_path,
            None,
            False,
            {},
        )
        try:
            server = HTTPServer(("127.0.0.1", 0), handler)
        except PermissionError as exc:
            pytest.skip(f"Local socket bind not permitted in this environment: {exc}")
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, port

    def test_serves_html_on_root(self, tmp_path):
        server, port = self._start_server(tmp_path)
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
            html = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in html
            assert "test-skill" in html
        finally:
            server.shutdown()

    def test_feedback_post_saves_file(self, tmp_path):
        server, port = self._start_server(tmp_path)
        try:
            feedback = {
                "reviews": [
                    {"test_index": 0, "task": "test", "feedback": "great work"},
                ],
                "status": "complete",
            }
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/feedback",
                data=json.dumps(feedback).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req)
            result = json.loads(resp.read())
            assert result["ok"] is True

            # Verify file was written
            saved = json.loads((tmp_path / "feedback.json").read_text(encoding="utf-8"))
            assert saved["reviews"][0]["feedback"] == "great work"
        finally:
            server.shutdown()

    def test_feedback_get_returns_saved(self, tmp_path):
        server, port = self._start_server(tmp_path)
        try:
            # First save feedback
            feedback = {"reviews": [{"feedback": "test"}], "status": "complete"}
            (tmp_path / "feedback.json").write_text(json.dumps(feedback), encoding="utf-8")

            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/feedback")
            data = json.loads(resp.read())
            assert data["reviews"][0]["feedback"] == "test"
        finally:
            server.shutdown()

    def test_404_on_unknown_path(self, tmp_path):
        server, port = self._start_server(tmp_path)
        try:
            with pytest.raises(urllib.error.HTTPError, match="404"):
                urllib.request.urlopen(f"http://127.0.0.1:{port}/nonexistent")
        finally:
            server.shutdown()

    def test_reloads_eval_data_on_refresh(self, tmp_path):
        server, port = self._start_server(tmp_path)
        try:
            # First request
            resp1 = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
            html1 = resp1.read().decode("utf-8")
            assert "is_prime" in html1

            # Update eval data
            updated = _sample_eval_data()
            updated["ab_outputs"][0]["with_skill_output"] = "UPDATED_OUTPUT_XYZ"
            (tmp_path / "eval-full.json").write_text(json.dumps(updated), encoding="utf-8")

            # Second request should pick up changes
            resp2 = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
            html2 = resp2.read().decode("utf-8")
            assert "UPDATED_OUTPUT_XYZ" in html2
        finally:
            server.shutdown()
