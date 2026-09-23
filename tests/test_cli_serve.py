from __future__ import annotations

from finance_context.cli import serve


def test_serve_starts_uvicorn_with_one_worker(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(*args, **kwargs) -> None:
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr("finance_context.cli.uvicorn.run", fake_run)
    serve(host="127.0.0.1", port=8080)
    assert seen["kwargs"]["workers"] == 1
    assert seen["kwargs"]["factory"] is True
    assert seen["args"] == ("finance_context.api.app:app_from_env",)
