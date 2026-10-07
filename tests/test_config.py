from pathlib import Path

from app.config import load_environment


def test_environment_wins_and_quotes_are_parsed(tmp_path):
    source = tmp_path / 'settings.env'
    source.write_text('GEMINI_API_KEY="test-only-key"\nGEMINI_MODEL=gemini-2.5-flash-lite\n')
    environment = {'GEMINI_API_KEY': 'process-only-key'}
    load_environment(source, environ=environment)
    assert environment == {'GEMINI_API_KEY': 'process-only-key', 'GEMINI_MODEL': 'gemini-2.5-flash-lite'}


def test_missing_file_and_invalid_lines_are_safe(tmp_path):
    environment = {}
    load_environment(tmp_path / 'missing.env', environ=environment)
    source = tmp_path / 'settings.env'
    source.write_text('# comment\nnot an assignment\nBAD-NAME=value\nexport HUELLA_PORT=8765\n')
    load_environment(source, environ=environment)
    assert environment == {'HUELLA_PORT': '8765'}


def test_default_location_is_repository_not_cwd(monkeypatch, tmp_path):
    from app import config
    visited = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'read_text', lambda path, **kwargs: visited.append(path) or '')
    load_environment(environ={})
    assert visited == [config.ROOT / '.env']
