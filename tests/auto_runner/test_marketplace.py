"""auto-runner plugin: the marketplace file points at the plugin by the manifest's name, and the hand-over docs exist."""
import json
from pathlib import Path

PLUGINS = Path(__file__).resolve().parent.parent.parent
PLUGIN = PLUGINS / 'auto-runner'


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_marketplace_plugin_name_matches_manifest():
    market = _json(PLUGINS / '.claude-plugin' / 'marketplace.json')
    manifest = _json(PLUGIN / '.claude-plugin' / 'plugin.json')
    assert market['name'] and market['owner']['name']
    assert [p['name'] for p in market['plugins']] == [manifest['name']]
    assert not manifest['name'].startswith(('claude-', 'anthropic-'))


def test_marketplace_source_is_the_plugin_folder():
    market = _json(PLUGINS / '.claude-plugin' / 'marketplace.json')
    for p in market['plugins']:
        src = (PLUGINS / p['source']).resolve()
        assert (src / '.claude-plugin' / 'plugin.json').is_file()


def test_docs_exist_and_migration_config_is_valid():
    import importlib.util
    import re
    assert (PLUGIN / 'README.md').is_file()
    text = (PLUGIN / 'MIGRATING-SIVA.md').read_text(encoding='utf-8')
    block = re.search(r'```json\n(.*?)\n```', text, re.S).group(1)
    cfg = json.loads(block)
    spec = importlib.util.spec_from_file_location('autocfg_mig', PLUGIN / 'bin' / 'autocfg.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(cfg) <= set(mod.DEFAULTS)
    for key, value in cfg.items():
        assert isinstance(value, type(mod.DEFAULTS[key])), key
    readme = (PLUGIN / 'README.md').read_text(encoding='utf-8')
    for key in mod.DEFAULTS:
        assert f'`{key}`' in readme, f'README does not document {key}'
