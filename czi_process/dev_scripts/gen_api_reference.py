"""One-off script: regenerates API_REFERENCE.md from the installed
rockface package via introspection (inspect module), so the reference
never drifts from source. Not part of the package -- run manually
after any public API change:

    python gen_api_reference.py
"""
import inspect
import subprocess
import sys

import rockface
from rockface import czi as czi_mod
from rockface import masks as masks_mod
from rockface import patching as patching_mod
from rockface import pipeline as pipeline_mod
from rockface import config as config_mod
from rockface import identity as identity_mod

lines = []


def h(level, text):
    lines.append(f"{'#' * level} {text}\n")


def sig_block(obj):
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        sig = "(...)"
    lines.append(f"```python\n{obj.__name__}{sig}\n```\n")


def doc_block(obj):
    doc = inspect.getdoc(obj) or "(no docstring)"
    lines.append(f"```\n{doc}\n```\n")


def method_section(cls, name, level=3):
    method = getattr(cls, name)
    h(level, f"`{cls.__name__}.{name}`")
    sig_block(method)
    doc_block(method)


def class_section(cls, level=2, public_methods=None):
    h(level, f"class `{cls.__module__}.{cls.__name__}`")
    doc_block(cls)
    h(level + 1, "Constructor")
    sig_block(cls.__init__)
    doc_block(cls.__init__)
    methods = public_methods or [
        name for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_") and inspect.getsourcefile(member) == inspect.getsourcefile(cls)
    ]
    # Preserve source definition order.
    src = inspect.getsource(cls)
    def_order = []
    for name in methods:
        idx = src.find(f"def {name}(")
        def_order.append((idx, name))
    def_order.sort()
    for _, name in def_order:
        method_section(cls, name, level=level + 1)


lines.append("# RockFace API Reference\n")
lines.append(
    "Generated via Python's `inspect` module against the installed "
    "package -- always reflects actual source, never hand-maintained "
    "prose. For task-oriented recipes instead of a full parameter "
    "listing, see [`USAGE.md`](./USAGE.md).\n"
)
h(2, "Overview")
lines.append("```python\n" + inspect.getdoc(rockface) + "\n```\n")

h(2, "Installation")
lines.append("```bash\npip install -e .\n```\n")

class_section(rockface.CZI)
class_section(rockface.Patching)
class_section(rockface.Mask)

h(2, "Module-level functions")

h(3, "`rockface.pipeline.run_pipeline`")
sig_block(pipeline_mod.run_pipeline)
doc_block(pipeline_mod.run_pipeline)

h(3, "`rockface.pipeline.cli_main`")
sig_block(pipeline_mod.cli_main)
doc_block(pipeline_mod.cli_main)

h(3, "`rockface.identity.slide_id`")
sig_block(identity_mod.slide_id)
doc_block(identity_mod.slide_id)

h(3, "`rockface.config.set_max_workers`")
sig_block(config_mod.set_max_workers)
doc_block(config_mod.set_max_workers)

h(3, "`rockface.config.resolve_max_workers`")
sig_block(config_mod.resolve_max_workers)
doc_block(config_mod.resolve_max_workers)

h(3, "`rockface.patching.find_channel_source`")
sig_block(patching_mod.find_channel_source)
doc_block(patching_mod.find_channel_source)

h(2, "Command-line interface")
lines.append("```console\n$ rockface --help\n")
try:
    result = subprocess.run([sys.executable, "-m", "rockface.pipeline", "--help"], capture_output=True, text=True, timeout=10)
    help_text = result.stdout or result.stderr
except Exception:
    # Fall back to calling cli_main's parser building directly.
    import argparse
    help_text = "(unable to capture --help output in this environment)"
lines.append(help_text)
lines.append("```\n")

h(2, "Advanced: module-level helper functions")
lines.append(
    "These back the multiprocessing workers and internal machinery -- "
    "not part of the stable public API, but documented here for anyone "
    "extending the package.\n"
)


def one_liners(mod):
    for name, member in inspect.getmembers(mod, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        if inspect.getsourcefile(member) != inspect.getsourcefile(mod):
            continue
        doc = inspect.getdoc(member) or ""
        summary = doc.split("\n")[0] if doc else "(no docstring)"
        lines.append(f"- `{mod.__name__}.{name}{inspect.signature(member)}` -- {summary}")
    lines.append("")


h(3, "`rockface.czi`")
one_liners(czi_mod)
h(3, "`rockface.masks`")
one_liners(masks_mod)

with open("API_REFERENCE.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("API_REFERENCE.md regenerated:", len("\n".join(lines)), "chars")
