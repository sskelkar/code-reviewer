# Reading this codebase if you know Java but not Python

This guide is for an experienced Java programmer who has never written Python and wants to read, run, debug and change this project. It does not teach Python in general. It covers exactly what this codebase uses, in the order you will meet it, and always says what the Java equivalent is.

Code samples are copied from this repository (some are abridged, marked with `...` or "simplified"); a few short samples are written purely to illustrate a language feature and are not from the repo (they are in sections 5, 8 and 15). The claims about this project were checked against the code. File links are relative to this `docs/` directory.

**How to use this guide**

| If you want to... | Read |
|---|---|
| Find where the program starts and how a request flows through it | [2](#2-where-the-program-starts), [11](#11-guided-tour-of-real-code) |
| Understand the build, packaging and dependencies (Maven/Gradle equivalents) | [3](#3-project-layout-packaging-and-tooling), [4](#4-libraries-used) |
| Read a Python file without stumbling over syntax | [5](#5-python-syntax-mapped-to-java), [6](#6-type-hints), [7](#7-functions) |
| Understand classes, inheritance and OOP here | [8](#8-classes-oop-and-inheritance) |
| Avoid the usual traps | [13](#13-gotchas-for-java-developers) |
| Have a quick lookup | [16](#16-java-to-python-cheat-sheet) |

---

## 1. The ten things that differ most

1. **Blocks are defined by indentation**, not braces. A colon starts a block; there are no semicolons.
2. **Python is dynamically typed.** Variables have no declared type; values do. Type annotations exist (and this project uses them heavily) but **nothing checks them at runtime** and no checker is configured in this repo. They are documentation plus editor help.
3. **There is no `new`, no `public/private/final/static`** (access is a naming convention), **no overloading, no interfaces and no checked exceptions.**
4. **Everything is an object, and functions are values.** You pass functions around as freely as strings.
5. **A file is a module** (a namespace). A directory with `__init__.py` is a package. There is no "one public class per file" rule.
6. **Instance fields are created by assignment in the constructor** (`self.x = ...`), not declared in the class body. The class body can also list fields for `@dataclass` classes (see 8.3).
7. **Duck typing replaces interfaces.** If an object has the method you call, it works. This project relies on that (see 8.7).
8. **Truthiness:** `None`, `0`, `""`, `[]`, `{}` and empty sets are all "false" in an `if`.
9. **Dictionaries (`dict`) are the default structured-data type.** Much of this project passes `Dict[str, Any]` (think `Map<String, Object>`) around instead of POJOs, because the data is JSON-shaped.
10. **Stack traces read bottom-to-top:** the most recent call is *last*, the opposite of Java.

---

## 2. Where the program starts

Java has `public static void main`. Python has several equivalents; this project uses three.

### 2.1 The `reviewer` command (the real entry point)

[`pyproject.toml`](../pyproject.toml) is the project descriptor (like `pom.xml`). It contains:

```toml
[project.scripts]
reviewer = "code_reviewer.cli:main"
```

That line tells the installer to generate an executable called `reviewer` that imports module `code_reviewer.cli` and calls its function `main`. It is the analogue of `Main-Class` in a jar manifest plus a launcher script. So **the starting point is the function `main` in [`src/code_reviewer/cli.py`](../src/code_reviewer/cli.py).**

`main` does this (simplified from the file):

```python
def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)        # parse command line (like picocli)
    cfg = load_config(args.config)                # read settings
    runner = ClaudeRunner(cfg.claude_bin, cfg.isolation_flags)

    if args.command == "doctor":                  # `reviewer doctor`
        return run_doctor(cfg, runner, live=args.live)
    if args.command == "review":                  # `reviewer review <url>`
        ...                                       # parse the PR, apply the options, print the banner in a terminal,
                                                  # call review_flow.run_review(...), print the summary, optionally post
    Session(cfg, runner).loop()                   # plain `reviewer`: the interactive prompt
    return 0
```

The return value is the process exit code. The last lines of `cli.py` are:

```python
if __name__ == "__main__":
    sys.exit(main())
```

`__name__` is `"__main__"` only when the file is run directly (`python -m code_reviewer.cli`); when another module imports it, the block is skipped. It is Python's `public static void main` guard, and you will see it at the bottom of many scripts.

### 2.2 The three frontends and the one engine

| You type | Entry | Goes to |
|---|---|---|
| `reviewer` | `cli.main` | [`repl.Session.loop`](../src/code_reviewer/repl.py): reads commands in a loop, `review <url>` calls `review_flow.run_review` |
| `reviewer review <url>` | `cli.main` | [`review_flow.run_review`](../src/code_reviewer/review_flow.py): the same steps the prompt's `review` command uses (pin the PR, then review or dry-run) |
| `reviewer doctor` | `cli.main` | [`doctor.run_doctor`](../src/code_reviewer/doctor.py): environment checks |

Both review paths go through `review_flow.run_review` and end in the same place: **[`Engine.review`](../src/code_reviewer/engine.py)** is the heart of the project, and the best first file to read.

### 2.3 Tests

`python3 -m unittest discover -s tests -t .` is the "run all tests" command (like `mvn test`). See [12](#12-tests).

### 2.4 One review, as a call stack

```
reviewer review <url>
 cli.main
  ├─ load_config()                         -> Config (settings object)
  ├─ ClaudeRunner(...)                     -> wrapper that runs `claude -p ...` as a subprocess
  └─ review_flow.run_review(...)           shared by the one-shot command and the interactive prompt
      ├─ Engine(cfg, runner, emit=ProgressPrinter())
      ├─ engine.prepare(ref)               -> pr_source.load_pr_context -> git_ops (runs gh and git)
      └─ engine.review(ctx)
          ├─ inputs.redact                 email addresses in the PR text -> [email]
          ├─ RunStore.create(...)          directory for this run's files
          ├─ inputs.load_diff              -> diffview.parse_unified_diff
          ├─ git_ops.export_tree           read-only copy of the PR's code, in its own temporary directory
          ├─ inputs.resolve_criteria       -> criteria.* (+ CriteriaResolver, an LLM call)
          ├─ build PassRunner(...)         manual wiring of collaborators (section 10.1)
          ├─ _run_passes: loop, asking StopPolicy whether to continue
          │    PassRunner.run
          │      ├─ Reviewer.review        -> ClaudeCaller.call -> ClaudeRunner.run  (`claude` process)
          │      ├─ FindingsPreparer.prepare   validate + check evidence
          │      ├─ Comparator.compare     -> another `claude` call (sometimes skipped)
          │      └─ MasterList.apply       merge into the master findings
          ├─ report.render_report          -> report.md (the detailed report)
          └─ summary.render_summary        -> summary.md (the shareable summary, also printed)
```

---

## 3. Project layout, packaging and tooling

### 3.1 Maven/Gradle versus pip

| Java world | Python world (this project) |
|---|---|
| `pom.xml` / `build.gradle` | [`pyproject.toml`](../pyproject.toml) |
| Maven Central | PyPI (not used here: the project has almost no dependencies) |
| `mvn` / `gradle` | `pip` installs, `python -m ...` runs |
| JDK + classpath | Python interpreter + `sys.path` |
| Per-project isolation (toolchains, `.m2`) | A **virtual environment** (`python3 -m venv .venv`) holding a private copy of installed packages |
| `mvn install`, then run the jar | `pip install -e .` ("editable install": the installed package points at your source folder, so edits take effect immediately) |
| Compilation to `.class` | None: Python runs source directly (it caches bytecode in `__pycache__/`, which you can ignore; it is git-ignored) |
| `src/main/java` | `src/code_reviewer` (a "src layout") |
| `src/test/java` + JUnit | `tests/` + `unittest` |

`pyproject.toml` declares the project name, the minimum Python version (`>=3.9`), the single conditional dependency (`tomli`, only on Python older than 3.11), and the `reviewer` command above.

### 3.2 Modules, packages and imports

- **Module** = one `.py` file. `engine.py` is module `code_reviewer.engine`.
- **Package** = a directory with an `__init__.py` file. `src/code_reviewer/` is the package `code_reviewer`.
- There is no package declaration at the top of a file; the directory structure *is* the package.
- A module can contain any number of classes, functions and constants. Free-standing functions are normal and are used heavily here (think "static utility methods", but without the enclosing class).

Imports:

```python
from pathlib import Path                      # standard library: import one name
import json                                   # import a module; use as json.loads(...)
from .config import Config                    # RELATIVE import: "config.py in the same package"
from . import git_ops                         # import a sibling module itself
from .templates import ROLES, load_prompt, prompt_hashes   # several names at once
```

The leading dot means "this package", so `from .config import Config` is `import code_reviewer.config.Config`. Imports are executed top to bottom when the module is first loaded, and each module is loaded once. **Circular imports fail at load time**, which is why the dependency direction matters here: `models`, `config` and `events` are at the bottom; `engine` and the frontends are at the top. `engine.py` imports `report.py`, never the other way around.

The first line of almost every file is:

```python
from __future__ import annotations
```

It makes type annotations lazy (they are stored as text and not evaluated), which lets the code use newer annotation syntax on older Python and refer to classes before they are defined. You can read it as a required incantation.

`engine.py` also has `__all__ = ["Engine", "PRContext", ...]`, which declares the module's public names (it controls what `from engine import *` pulls in and tells readers what is meant to be used). `engine.py` imports `ReviewRefused` and `PRContext` from `models.py`, so they are also reachable as `engine.ReviewRefused`; the tests and the REPL rely on that re-export. Importing a name that another module merely imported is allowed in Python, whether or not `__all__` lists it.

### 3.3 The `src/` layout and `tests/__init__.py`

Python finds modules through `sys.path` (the classpath analogue). After `pip install -e .`, `src/` is on it. Tests run without installing too, because [`tests/__init__.py`](../tests/__init__.py) adds `src/` to `sys.path` itself; that is what the `-t .` flag in the test command enables.

---

## 4. Libraries used

### 4.1 Third party

Exactly one, and only on older Pythons: **`tomli`**, a TOML file parser. Python 3.11 added the same thing to the standard library as `tomllib`. [`config.py`](../src/code_reviewer/config.py) tries `tomllib` and falls back to `tomli`:

```python
def _read_toml(path: Path) -> Dict[str, Any]:
    try:
        import tomllib as toml_lib
    except ModuleNotFoundError:
        try:
            import tomli as toml_lib
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "reading %s needs Python 3.11+ or the 'tomli' package (pip install tomli)" % path
            ) from exc
    with open(path, "rb") as handle:
        return toml_lib.load(handle)
```

That is the Python way to say "use this library if present, otherwise that one" (in Java you would catch `ClassNotFoundException` around `Class.forName`). An import statement can sit inside a function and can fail with an exception.

### 4.2 The standard library (the "JDK" part)

The project deliberately uses only the standard library. This is what each module is for:

| Module | Purpose | Java analogue | Mainly used in |
|---|---|---|---|
| `subprocess` | Run external programs (`claude`, `git`, `gh`) | `ProcessBuilder` | `claude_cli.py`, `git_ops.py`, `doctor.py` |
| `json` | Parse and write JSON | Jackson / Gson | many (`claude_cli`, `run_store`, `engine`, ...) |
| `pathlib` | File paths as objects; `path / "x"` joins | `java.nio.file.Path` + `Files` | nearly every module |
| `re` | Regular expressions | `java.util.regex` | `diffview.py`, `criteria.py`, `report.py`, `redact.py` |
| `dataclasses` | Auto-generate data-holder classes | records / Lombok `@Data` | `models.py`, `config.py`, ... |
| `typing` | Type-hint vocabulary (`List`, `Dict`, `Optional`, ...) | Generics syntax | everywhere |
| `argparse` | Command-line parsing | picocli, commons-cli | `cli.py`, `review_options.py`, `replay.py` |
| `shlex` | Split a command line honoring quotes | none direct | `repl.py` |
| `tempfile` | Temporary files and directories | `Files.createTempFile/Directory` | `run_store.py`, `git_ops.py`, `post.py`, `doctor.py` |
| `shutil` | High-level file ops (delete a tree) | `FileUtils` | `run_store.py`, `doctor.py` |
| `tarfile` | Read `.tar` archives | commons-compress | `git_ops.py` |
| `hashlib` | SHA-256 hashing | `MessageDigest` | `templates.py` |
| `fnmatch` | Shell-style glob matching (`*.min.js`) | `PathMatcher` | `diffview.py` |
| `itertools` | Iteration helpers (`combinations`) | Streams helpers | `master.py`, `replay.py` |
| `copy` | Deep copies | copy constructors | `master.py` |
| `time`, `platform`, `os`, `sys` | Clock, interpreter info, environment variables, stdout/exit | `System`, `Instant` | several |
| `readline` | Line editing and history for `input()` | JLine | `repl.py` (optional import) |
| `unittest` | Test framework | JUnit | `tests/` |

### 4.3 External programs, not libraries

The agent drives three command-line tools by running them as processes and reading their output: **`claude`** (Claude Code, in headless mode), **`gh`** (GitHub CLI) and **`git`**. No HTTP client, no GitHub SDK, no LLM SDK is used. The wrappers are [`claude_cli.py`](../src/code_reviewer/claude_cli.py) and [`git_ops.py`](../src/code_reviewer/git_ops.py).

### 4.4 What you might expect but will not find

| Java expectation | Here |
|---|---|
| Spring or another DI container | None. Objects are wired by hand with constructor arguments (section 10.1) |
| Lombok / records | `@dataclass` |
| Jackson POJOs | Plain `dict`/`list` from `json` |
| Mockito | Hand-written fakes (`FakeRunner`), section 12 |
| SLF4J/log4j | No logging framework. Progress is printed through events; details go to files in the run directory |
| Async/threads | None. Everything is sequential |
| A web framework or database | None |

---

## 5. Python syntax mapped to Java

### 5.1 Basics

```python
count = 0                      # no declaration, no type, no semicolon
name: str = "x"                # optional annotation (documentation)
if count > 0 and name:         # and / or / not instead of && || !
    print("positive")          # block = indentation (4 spaces)
elif count == 0:               # else if
    pass                       # empty block placeholder
else:
    print("negative")
for item in items:             # for-each; there is no C-style for(;;)
    ...
for i in range(1, 7):          # 1,2,...,6 (end is exclusive)
    ...
while condition:
    ...
x = a if cond else b           # ternary: value-if-true first
```

- `None` is `null`. Test it with `is None` / `is not None`.
- `True`/`False`/`None` are capitalized.
- `==` compares values (like `equals`), `is` compares identity (like `==` on references). Use `is` only for `None`.
- `in` tests membership: `"x" in some_list`, `key in some_dict`, `"ab" in "cab"`.
- Chained comparisons work: `0 < x <= 1`.
- Comments are `# ...`. Docstrings are `"""..."""` string literals as the first statement of a module, class or function; they are the Javadoc analogue and are available at runtime.
- Strings use `'` or `"`; `"""` spans lines; `r"..."` is a raw string (backslashes are literal, used for regexes such as `re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")`). Adjacent string literals inside parentheses are concatenated automatically (see `LIVE_PROMPT` in `doctor.py`).
- Numbers: integers are unbounded; `/` is always floating-point division; `//` is integer division.

### 5.2 String formatting

This project uses **printf-style `%` formatting** everywhere, the same placeholders as `String.format`:

```python
"%s#%d" % (self.repo, self.number)     # like String.format("%s#%d", repo, number)
"%.2f" % budget_usd
"error: %s" % exc                      # a single value needs no tuple
```

Newer Python has f-strings (`f"{repo}#{number}"`); this codebase does not use them, which keeps it consistent with 3.9 style. `"-".join(parts)` is `String.join("-", parts)`; `text.split(",")`, `.strip()`, `.lower()`, `.startswith()` are what you expect.

### 5.3 Collections

| Python | Java | Notes |
|---|---|---|
| `list` `[1, 2]` | `ArrayList` | mutable |
| `dict` `{"a": 1}` | `LinkedHashMap` | **keeps insertion order** |
| `set` `{1, 2}` | `HashSet` | supports `&` (intersection), `\|` (union), `-` (difference). `master.py` uses `self.scored_ids() - scored_before` |
| `tuple` `(1, 2)` | immutable fixed-size list / record | `return True, "msg", 3` returns a tuple |
| `str`, `bytes` | `String`, `byte[]` | strings are immutable |

```python
items[0]            # first
items[-1]           # LAST element (negative index counts from the end)
items[:12]          # first 12 (like substring(0, 12) but NEVER throws if shorter)
mapping["k"]        # KeyError if absent
mapping.get("k")    # None if absent (get-or-null)
mapping.get("k", 5) # default value
```

Creating and unpacking:

```python
a, b = pair                              # destructuring a tuple
meta, _, path = entry.partition(b"\t")   # splits into 3 parts; `_` is a throwaway name
self.caller, self.prompt = caller, prompt   # parallel assignment
merged = {**stats.to_dict(), "passes": passes}   # dict with another dict spread into it
cmd = ["gh", "api", *extra_args, "user"]         # list with another list spread into it
```

**Comprehensions** are the compact form of "loop and collect" (like a stream `filter` + `map` + `collect`):

```python
[f for f in files if not f.excluded]              # list of f where condition holds
[f.path for f in files if f.excluded]             # map + filter
{t: [] for t in TIERS}                            # dict comprehension
[line.text for hunk in f.hunks for line in hunk.lines if line.kind == "-"]   # nested loops
```

Useful built-ins you will see: `len(x)`, `sorted(x, key=...)`, `x.sort(key=...)` (in place), `min/max(x, key=...)`, `any(...)`/`all(...)`, `sum(...)`, `enumerate(x, start=1)` (index + value), `isinstance(v, dict)`, `dict(other)` (shallow copy), `str(v)`, `int(v)`.

`key=` takes a function that extracts the value to compare by, e.g. in [`report.py`](../src/code_reviewer/report.py):

```python
for items in groups.values():
    items.sort(key=lambda i: (-SEVERITY_RANK.get(i["canonical"]["severity"], 0), i["id"]))
```

A **lambda** is an anonymous one-expression function (`i -> ...` in Java). Here it returns a tuple, and tuples compare element by element, so this sorts by severity descending, then by id.

### 5.4 Truthiness and the `or` default idiom

```python
if not findings:                   # true for None AND for an empty list
return result.parsed or {}         # "parsed, or an empty dict if it is None/empty"
stated = finding.get("line") or starts[0]   # use starts[0] when line is None OR 0
```

`a or b` returns `a` if `a` is truthy, otherwise `b` (not just a boolean). It is used as a default operator. Beware that `0` and `""` count as missing; the code is written with that in mind.

### 5.5 Naming and visibility

- Functions, variables, modules: `snake_case`. Classes: `PascalCase`. Constants: `UPPER_SNAKE_CASE` (not enforced, there is no `final`).
- **A leading underscore means "private by convention"**: `_run`, `_ground_quote`, `Reviewer._variables`. Nothing prevents access; it is a signal to readers. Names without it are the module's public surface. (Honest note: `post.py` imports `_run` from `git_ops`, which bends the convention.)
- Names with double underscores on both sides (`__init__`, `__call__`, `__str__`, `__name__`) are special hooks defined by the language ("dunder" methods, section 8).
- A trailing underscore (`type_` in `Engine.emit(self, type_, ...)`) avoids clashing with a built-in name (`type`).

---

## 6. Type hints

Annotations such as `repo: str` or `-> int` are everywhere. They are **not enforced**. They tell the reader (and editors/type checkers) what is expected.

| Annotation | Java |
|---|---|
| `x: int`, `str`, `float`, `bool` | `int`, `String`, `double`, `boolean` |
| `List[str]` | `List<String>` |
| `Dict[str, Any]` | `Map<String, Object>` |
| `Set[str]` | `Set<String>` |
| `Optional[str]` | `@Nullable String` (it means `str` or `None`) |
| `Tuple[bool, str, Optional[int]]` | a 3-element fixed return, like a small record |
| `Sequence[str]` | read-only ordered collection (accepts list or tuple), like `List<String>` used read-only |
| `Callable[[Event], None]` | `Consumer<Event>` |
| `Callable[[Dict[str, Any]], List[str]]` | `Function<Map<String,Object>, List<String>>` |
| `Any` | `Object` with no checking at all |
| `-> None` | `void` |

Two things to notice in this repo:

- **Type aliases** are plain variable assignments: `EventSink = Callable[[Event], None]` ([`events.py`](../src/code_reviewer/events.py)) and `Validator = Callable[[Dict[str, Any]], List[str]]` ([`llm.py`](../src/code_reviewer/llm.py)). They name a function shape, like declaring a functional interface.
- A class name in quotes (`-> "RunStore"`) is a forward reference to a class that is not fully defined yet at that point.

Because hints are not checked, a wrong hint cannot fail at runtime. The unit tests, not a compiler, are what keep this code honest. That is also why the test suite is large relative to the code size.

---

## 7. Functions

### 7.1 Parameters

```python
def emit(self, type_: str, **data: Any) -> None:     # **data collects extra keyword args into a dict
    self.sink(Event(type_, data))

self.emit("diff_ready", files=3, excluded=1)         # call with keyword arguments
```

- **Default values:** `def f(a, b=5)`; `Optional[X] = None` is the usual "optional parameter" (there is no overloading, defaults replace it).
- **Keyword arguments** let you name parameters at the call site, which this code uses constantly for readability: `CallSpec(role="resolver", pass_no=None, ...)`.
- **Keyword-only parameters**: everything after a bare `*` must be passed by name:

```python
def build_command(
    claude_bin: str,
    *,
    system_prompt: str,
    schema: Dict[str, Any],
    model: str,
    ...
```

  `build_command("claude", system_prompt="...", model="haiku", ...)` is valid; passing `system_prompt` positionally is an error. It makes calls with many parameters self-documenting.
- **`*args` / `**kwargs`**: `*keys` collects extra positional arguments into a tuple (varargs `String... keys`); `**data` collects extra keyword arguments into a dict.
- **Multiple return values** are just a returned tuple, unpacked by the caller: `grounded, why, corrected = ground(finding, cache, diff)`.
- A function without `return` returns `None`.

### 7.2 Functions are values, closures, and `nonlocal`

```python
def requires_lists(*keys: str) -> Validator:
    """Validator: the output must be an object whose given keys are lists."""

    def validate(parsed: Dict[str, Any]) -> List[str]:
        return ["output must contain a '%s' list" % key for key in keys if not isinstance(parsed.get(key), list)]

    return validate
```

`requires_lists` builds and returns a function. The inner `validate` captures `keys` from the enclosing scope (a closure, like a lambda capturing a variable). Callers do `requires_lists("findings")` and get a validator to pass around. In Java this would be a method returning a `Function<...>`.

Java lambdas cannot reassign captured locals; Python inner functions can, if they declare `nonlocal` (see `redact.redact_emails`, where the nested `replace` function increments a `count` declared in the enclosing function).

### 7.3 Callable objects

Any object whose class defines `__call__` can be invoked like a function. `ProgressPrinter` (in `progress.py`) and `CriteriaResolver` (in `roles.py`) do this, so an instance can be passed where a function is expected:

```python
engine = Engine(cfg, runner, emit=ProgressPrinter(verbose=options.verbose))   # emit expects a function Event -> None (review_flow.run_review)
```

This is how Java would use a class implementing `Consumer<Event>`, minus the interface declaration.

---

## 8. Classes, OOP and inheritance

### 8.1 The short answer

> **Inheritance in this project is used in three ways:** custom exception types (`class ReviewRefused(ValueError)`), unit tests (`class X(unittest.TestCase)`, sometimes with a mixin), and one subclass of a standard-library class that overrides a single method (`ReplArgumentParser`, section 8.6). There are **no abstract classes, no interfaces, no `super()` calls, no class hierarchies and no metaclasses** in the production code. Behavior is shared by **composition** (objects holding other objects) and by passing **functions** around.

If you are used to deep Java hierarchies, expect something flatter here.

### 8.2 A plain class

From [`stop_policy.py`](../src/code_reviewer/stop_policy.py):

```python
class StopPolicy:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.completed = 0
        self.quiet_streak = 0
        self.failures_in_row = 0

    def before_pass(self, over_budget: bool) -> Optional[str]:
        return BUDGET_EXCEEDED if over_budget else None

    def after_success(self, new_scored: int) -> Optional[str]:
        self.failures_in_row = 0
        self.completed += 1
        self.quiet_streak = self.quiet_streak + 1 if new_scored == 0 else 0
        if self.completed >= self.cfg.min_passes and self.quiet_streak >= self.cfg.quiet_k:
            return CONVERGED
        return None
```

Mapping to Java:

| Python | Java |
|---|---|
| `class StopPolicy:` | `class StopPolicy` (no braces; the indented block is the body) |
| `def __init__(self, cfg)` | the **constructor** |
| `StopPolicy(cfg)` | `new StopPolicy(cfg)`: you call the class like a function; there is no `new` |
| `self` | `this`, but it is an **explicit first parameter of every instance method**. You write `self.completed`, never just `completed` |
| `self.completed = 0` in `__init__` | the field declaration **and** initialization. Fields do not need to be declared in the class body |
| `+=`, no `++` | `x += 1` (there is no `++` operator) |
| Constants `CONVERGED` | module-level names defined above the class |

Methods are called as `policy.after_success(2)`; Python passes `policy` as `self` for you.

### 8.3 `@dataclass`: data holders without boilerplate

From [`models.py`](../src/code_reviewer/models.py):

```python
@dataclass
class PRContext:
    """A pull request pinned to exact commits."""

    repo: str
    number: int
    url: str
    ...
    git_dir: Path
    host: str = "github.com"
    author: str = ""
    state: str = ""

    @property
    def label(self) -> str:
        return "%s#%d" % (self.repo, self.number)
```

`@dataclass` is a **decorator** (see 8.4) that reads the annotated names in the class body and generates the constructor, `toString` (`__repr__`) and `equals` (`__eq__`) for you. It is the Python analogue of a Java `record` or Lombok `@Data`. Things to know:

- Fields are listed in the class body with their types. The generated constructor takes them **in that order**, and fields with defaults must come after fields without. Because the generated constructor accepts keyword arguments, the code builds these objects by name:
  `PRContext(repo=ref.slug, number=ref.number, url=..., ...)`.
- Unlike a Java `record`, a dataclass is **mutable** by default: `cfg.max_passes = 3` works. `@dataclass(frozen=True)` makes it immutable (used for `PRRef` and `LocalRepo` in `git_ops.py`).
- **Mutable defaults need a factory:**

  ```python
  disabled_default_rules: List[str] = field(default_factory=list)
  ```

  In Python, a default value is evaluated **once** and shared. `= []` would give every instance the same list (and `@dataclass` refuses it). `default_factory=list` calls `list()` for each new object. This is the idiom you will see in `Config`, `PassRecord` and others.
- Helpers from the `dataclasses` module: `asdict(obj)` turns an instance into a nested `dict` (used for JSON output, e.g. `PassRecord.to_dict`) and `replace(obj, ...)` makes a shallow copy (`cli.py` does `cfg = replace(cfg)` to avoid mutating shared settings).
- There are 22 dataclasses in this project, for example `Config`, `PRContext`, `PassRecord`, `RunResult`, `CallSpec`, `ClaudeResult`, `Event`, `Rule`.

### 8.4 Decorators

A decorator is written `@name` above a function or class. It looks like a Java annotation, **but it is not metadata: it is a function that receives the decorated function or class and returns a replacement.** `@dataclass` transforms the class by adding methods. The decorators used here:

| Decorator | Meaning | Java analogue |
|---|---|---|
| `@dataclass` | Generate constructor, `__repr__`, `__eq__` from the field list | `record` / Lombok `@Data` |
| `@property` | Method accessed **without parentheses**: `ctx.label`, not `ctx.label()` | A getter, but called like a field |
| `@staticmethod` | Method that does not receive `self` | `static` method |
| `@classmethod` | Method that receives the **class** (`cls`) instead of an instance | A static factory method |

`@staticmethod`, from `Comparator` in [`roles.py`](../src/code_reviewer/roles.py):

```python
@staticmethod
def is_trivial(findings: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]]) -> bool:
    """With nothing to compare against (or no findings) every finding is new and no model call is needed."""
    return not findings or (not candidates and len(findings) == 1)
```

`@classmethod`, from [`run_store.py`](../src/code_reviewer/run_store.py), a factory that builds the object:

```python
@classmethod
def create(cls, runs_path: Path, ctx: PRContext, label: str = "") -> "RunStore":
    ...
    path.mkdir()
    return cls(path)          # cls is the class itself; cls(path) is `new RunStore(path)`
```

It is called on the class itself, `RunStore.create(...)`, exactly like a Java static factory method (`RunStore.create(...)`); the only difference is that the method also receives the class as `cls`, which matters when subclasses exist.

### 8.5 Special ("dunder") methods used

| Method | Triggered by | Java analogue | Where |
|---|---|---|---|
| `__init__` | `ClassName(...)` | constructor | all classes |
| `__call__` | `obj(...)` | implementing a functional interface | `ProgressPrinter`, `CriteriaResolver` |
| `__str__` | `str(obj)` / `"%s" % obj` | `toString()` | `PRRef` in `git_ops.py` |
| `__eq__`, `__repr__` | `==`, debugging output | `equals`, `toString` | auto-generated by `@dataclass` |

Operator overloading exists in Python and is why `Path` objects use `/`: `self.work_dir / "resolver.raw.json"` is "append a path segment" (the class defines `__truediv__`). It reads like Java's `Path.resolve`.

### 8.6 Inheritance, as it is actually used here

**Exceptions** ([`models.py`](../src/code_reviewer/models.py), [`git_ops.py`](../src/code_reviewer/git_ops.py), [`claude_cli.py`](../src/code_reviewer/claude_cli.py), [`config.py`](../src/code_reviewer/config.py), [`review_options.py`](../src/code_reviewer/review_options.py)):

```python
class ReviewRefused(ValueError):
    """The PR cannot be reviewed (too large, nothing reviewable, ...)."""

class PassFailed(RuntimeError):
    """One pass could not produce a usable result."""
```

The parent class goes in parentheses: `class B(A)` is `class B extends A`. These classes have an empty body apart from a docstring, because all they need is their own *type*, so callers can catch them separately. The parents (`ValueError`, `RuntimeError`) are built-in; the closest Java mapping is `IllegalArgumentException` and `RuntimeException`. All Python exceptions are unchecked.

**Tests** ([`tests/helpers.py`](../tests/helpers.py) and the test modules):

```python
class TempDirTestMixin:
    def make_tmp(self) -> Path:
        path = tempfile.mkdtemp(prefix="code-reviewer-test-")
        self.addCleanup(shutil.rmtree, path, True)
        return Path(path)

class EngineTests(TempDirTestMixin, unittest.TestCase):
    ...
```

This is **multiple inheritance**: `EngineTests` extends both. Python allows any number of parent classes; they are searched left to right (the "method resolution order") when a method is looked up. `TempDirTestMixin` is a **mixin**: a small class that adds a helper method and is not meant to be used alone, much like a Java interface with a default method. It calls `self.addCleanup`, which only exists on its sibling `unittest.TestCase`; that works because Python resolves the call on the actual object at runtime (duck typing).

**A subclass that overrides one method** ([`review_options.py`](../src/code_reviewer/review_options.py)):

```python
class ReplArgumentParser(argparse.ArgumentParser):
    """An argparse parser that raises OptionError on bad input instead of printing usage and exiting."""

    def error(self, message: str) -> Any:
        raise OptionError(message)
```

This is the one place where a method is **overridden**. The standard library's `ArgumentParser.error` prints a usage message and exits the whole process, which is right for a command-line tool but wrong inside the interactive prompt, which must stay alive. The subclass replaces `error` with a version that raises an exception the prompt can catch and report. In Java this is `extends ArgumentParser` plus an `@Override` method. Python has no `@Override`: an override is simply a method with the same name, and nothing checks that the name matches, so a typo would silently create a new method instead of overriding. It also needs no `super()` call because it does not reuse the parent's behavior.

### 8.7 Polymorphism without interfaces (duck typing)

Java would define `interface ClaudeRunner` and two implementations. Python does not need that. `Engine` takes `runner: Any`, and anything with a matching `run(...)` method works:

- Production: `ClaudeRunner` ([`claude_cli.py`](../src/code_reviewer/claude_cli.py)) launches the real `claude` process.
- Tests: `FakeRunner` ([`tests/helpers.py`](../tests/helpers.py)) returns scripted answers.

The two classes share no parent. This structural typing ("if it walks like a duck...") is how all the test doubles work, and why no mocking framework is needed. It also means there is no compile-time check that a replacement is complete; the tests catch that. You will see runtime capability checks such as `hasattr(self.runner, "version")` in `Engine._run_metadata`.

### 8.8 Encapsulation, statics and enums

- **No `private`/`protected`/`public`.** Convention only (leading underscore).
- **No `static` keyword.** Module-level functions and constants take its place. A function that does not need an object is just a function in the module, for example `findings._norm`. `@staticmethod` is used only when a function belongs with a class, such as `Comparator.is_trivial`.
- **Class-level constants** live in the class body, e.g. `Reviewer.TOOLS = "Read,Grep,Glob"`, accessed as `self.TOOLS` or `Reviewer.TOOLS`.
- **No enums are used.** States are strings such as `"converged"`, kept in constants (`CONVERGED = "converged"` in [`stop_policy.py`](../src/code_reviewer/stop_policy.py)). Python has `enum.Enum`, but plain strings keep the JSON output simple. Where Java would use an enum, check the nearby constants instead.

### 8.9 Dicts instead of POJOs

Most of the "findings" data is a `Dict[str, Any]`, not a class. A finding is built in [`findings.py`](../src/code_reviewer/findings.py) as a dict with keys like `"title"`, `"severity"`, `"quote"`, and the master list stores dicts with `"seen_in_passes"` and so on. The reasons are practical: the data arrives as JSON from the model, goes back out as JSON, and has optional fields. The cost is that you lose compile-time field checking and must rely on tests and on reading the key names (listed in `findings._normalize` and `master.MasterList._new_item`). Where the data has a fixed, code-owned shape (settings, per-pass records, a call specification), a `@dataclass` is used. If you are tempted to refactor findings into classes, that is a reasonable change; the tests would guide it.

Related: `getattr(p, field)` (read an attribute by its name, in `report._counters_section`) and `outcome.__dict__` (an object's fields as a dict, in `passes.py`) are Python's lightweight reflection.

---

## 9. Exceptions and resource handling

```python
try:
    return self._attempts(spec, validate, raw_log)
finally:
    dump_json(raw_path, raw_log)          # always runs, like Java's finally
```

(from `llm.ClaudeCaller.call`). Other forms in this code:

```python
REVIEW_ERRORS = (GitError, ConfigError, ReviewRefused, ClaudeError)   # review_flow.py: a tuple of exception classes

except REVIEW_ERRORS as exc:                                          # multi-catch, binds the exception to `exc`
    print("error: %s" % exc, file=sys.stderr)

raise GitError("command not found: %s" % cmd[0]) from exc     # `from exc` chains the cause (Java: new X(msg, cause))
```

- A tuple of exception classes can be named once and reused in `except`, like a Java multi-catch that you can name and share between callers.
- `raise` is `throw`; `except` is `catch`. There is **no `throws` clause and no checked exceptions.** Any function may raise anything.
- `except Exception` catches almost everything but not `KeyboardInterrupt` (Ctrl-C) or `SystemExit`; those derive from `BaseException`. `engine.py` catches `KeyboardInterrupt` deliberately so Ctrl-C ends a review gracefully with a partial report.
- Python culture prefers "try it and handle the failure" over pre-checking; `json.loads` is called inside `try`, for example.

**`with` is try-with-resources.** From `config.py`:

```python
with open(path, "rb") as handle:
    return toml_lib.load(handle)
```

The file is closed automatically when the block ends, even if an exception occurs. Any object implementing the context-manager protocol works (`tempfile.TemporaryDirectory()`, `tarfile.open(...)`, ...).

---

## 10. How the code is wired

### 10.1 Manual dependency injection

There is no framework. Collaborators are passed through constructors, and `Engine.review` is the composition root:

```python
pass_runner = PassRunner(
    cfg,
    Reviewer(caller, self.prompts["reviewer"], cfg, review_input),
    Comparator(caller, self.prompts["comparator"], cfg, store.input_dir),
    FindingsPreparer(criteria.index(), cfg.self_check, FileCache(checkout), DiffIndex(files), cfg.grounding_line_window),
    master, stats, store, self.emit,
)
```

This is what a Spring `@Configuration` class would do. It is also why classes are easy to unit test: pass in fakes.

### 10.2 Events and callbacks (observer pattern, without an interface)

`Engine` reports progress by calling `self.emit("pass_finished", pass_no=2, new=1, ...)`, which creates an `Event(type, data)` and hands it to whatever function was supplied as `emit=`. The CLI passes a `ProgressPrinter`, which prints a line. Other frontends (or tests) could pass a different function. The engine never prints. See [`events.py`](../src/code_reviewer/events.py) and [`progress.py`](../src/code_reviewer/progress.py).

### 10.3 The central design rule

Language models only *propose*; plain code validates and decides. Every model reply passes through a validator and the harness writes all files itself. When you see `Validator`, `FindingsPreparer`, `validate_decisions`, or a `*.raw.json` file, that rule is at work. See [`proposal.md`](proposal.md) for the reasoning.

### 10.4 Module-level layout of a typical file

A typical module here reads top to bottom as: docstring, `from __future__ import annotations`, imports, constants, small helper functions (underscore-prefixed), dataclasses/classes, then public functions. There is no required order, but this repository follows it.

---

## 11. Guided tour of real code

### 11.1 `Engine.review`: read this first

[`engine.py`](../src/code_reviewer/engine.py), about 45 lines. Read it as a recipe; each line delegates to a module:

```python
def review(self, ctx: PRContext, keep_checkout: bool = False) -> RunResult:
    cfg = self.cfg
    ctx, redacted_emails = self.inputs.redact(ctx)           # email addresses in the PR text become [email]
    self.emit("run_started", repo=ctx.repo, ...)             # tell the frontend
    stats = Stats()                                          # cost accounting
    store = RunStore.create(cfg.runs_path(), ctx)            # directory for artifacts
    store.dump("config.json", self._run_metadata(ctx))       # remember settings and versions
    store.save_pr_text(ctx, redacted_emails)                 # the PR text as it is sent to the model

    files, diff_summary = self.inputs.load_diff(ctx)         # parse the diff (tuple return, unpacked)
    checkout = store.prepare_checkout()                      # a temporary directory outside the run directory
    try:
        self.emit("checkout_ready", path=str(checkout), kept=keep_checkout,
                  **git_ops.export_tree(ctx.git_dir, ctx.head_sha, checkout))   # ** spreads a dict into keyword arguments

        caller = ClaudeCaller(self.runner, cfg, stats)       # all LLM calls go through this
        criteria = self.inputs.resolve_criteria(ctx, files, caller, store.input_dir)
        ...
        passes, completed, stop_reason = self._run_passes(pass_runner, stats)
        ...
        return result
    finally:
        if not keep_checkout:                                # runs on success and on errors alike
            store.cleanup_checkout()
```

Notes for a Java reader: `**git_ops.export_tree(...)` expands the returned dict into named arguments (`files=..., skipped_links=...`); assignments such as `ctx, redacted_emails = ...` and `files, diff_summary = ...` unpack a returned tuple; `try/finally` works as in Java, so the checkout is deleted even if an exception escapes the method.

### 11.2 `StopPolicy`: a small pure class

Shown in 8.2. It has no I/O, only counters and rules, so [`tests/test_stop_policy.py`](../tests/test_stop_policy.py) can exercise the stopping rules directly. Good first file to read to get used to Python class syntax.

### 11.3 `ClaudeCaller`: retry, try/finally

[`llm.py`](../src/code_reviewer/llm.py) wraps every model call: run, record usage, validate the result with a supplied function, retry once with feedback if invalid, always write the raw output to disk (`finally`). It shows passing functions as arguments (`validate: Validator`) and the `Validator` alias.

### 11.4 `CriteriaResolver.__call__`: a callable object

[`roles.py`](../src/code_reviewer/roles.py). A class whose instances are called like functions:

```python
resolver = CriteriaResolver(caller, prompt, cfg, ctx, work_dir, emit)
manifest = resolver(candidates, agents_text)        # runs __call__
```

and `criteria.resolve_project` simply accepts "a function that takes (list, str) and returns a dict" (`call_resolver: Callable[[List[str], str], Dict[str, Any]]`), so in tests a plain `lambda` can stand in.

### 11.5 `ClaudeRunner`: talking to an external process

[`claude_cli.py`](../src/code_reviewer/claude_cli.py): `build_command(...)` assembles the argument list, `ClaudeRunner.run` calls `subprocess.run(cmd, input=user_prompt, capture_output=True, text=True, timeout=...)` (like `ProcessBuilder` with stdin/stdout capture and a timeout), and `parse_envelope` decodes the JSON that `claude -p --output-format json` prints into a `ClaudeResult` dataclass. Note it runs one-shot processes; there is no server and no network client in this code.

### 11.6 Reading a regex

`criteria.py` and `diffview.py` use `re.compile(...)` objects, e.g. `_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")`, then `match = _HEADING.match(line)` and `match.group(1)`. This is `Pattern`/`Matcher`; a `None` result means no match, so you will see `if match:` checks.

---

## 12. Tests

[`unittest`](https://docs.python.org/3/library/unittest.html) is the standard library's JUnit-style framework.

```python
class StopPolicyTests(unittest.TestCase):
    def test_converges_after_min_passes_and_quiet_streak(self):
        p = policy(min_passes=3, quiet_k=2)
        self.assertIsNone(p.after_success(2))
        self.assertIsNone(p.after_success(0))
        self.assertEqual(p.after_success(0), CONVERGED)
```

| JUnit | unittest |
|---|---|
| `@Test` | A method whose name **starts with `test_`** in a class extending `unittest.TestCase` |
| `@BeforeEach` | `setUp(self)` |
| `@AfterEach` | `tearDown(self)`, or `self.addCleanup(fn, args)` which runs `fn` after the test even on failure |
| `assertEquals(expected, actual)` | `self.assertEqual(a, b)` (symmetric; the failure message shows both) |
| `assertThrows` | `with self.assertRaises(SomeError):` |
| Mockito | Hand-written fakes: `FakeRunner` in `tests/helpers.py` |
| Test discovery | `python3 -m unittest discover -s tests -t .` finds `test_*.py` files |

Reading the tests is the fastest way to learn what each module is supposed to do. `tests/helpers.py` builds a real throwaway git repository (`make_repo`) so engine tests run the real code against real git, and scripts the model through `FakeRunner`. `test_runner_plumbing.py` goes further and uses a tiny fake `claude` executable script to test the subprocess code.

---

## 13. Gotchas for Java developers

1. **Indentation is syntax.** Mixing tabs and spaces, or mis-indenting a line, changes meaning or breaks the file. This repo uses 4 spaces.
2. **Mutable default arguments** are shared across calls. Never write `def f(x=[])`; use `None` or `default_factory` (8.3).
3. **`is` versus `==`.** Use `==` for values. Use `is` only against `None`.
4. **Truthiness surprises.** `if x:` is false for `0`, `""`, `[]`, `{}`. Use `if x is not None:` when `0` or empty is a valid value. In `findings.py`, `isinstance(value, bool)` is checked **before** `int`, because `bool` is a subclass of `int` in Python (`True == 1`).
5. **Assignment never copies.** `b = a` makes two names for one object; copy a dict with `dict(a)` (`Comparator.all_new` does `dict(NEW_DECISION)`), deep copy with `copy.deepcopy`.
6. **`self` is explicit.** Forgetting it in a method signature or call gives confusing errors ("takes 1 positional argument but 2 were given").
7. **Typos in names fail at runtime**, not compile time. Run the tests after changes; a misspelled attribute only fails when that line executes.
8. **No overloading.** One name, one function; use default arguments and keyword arguments.
9. **A missing dict key raises `KeyError`.** Use `.get(key)` when the key may be absent.
10. **`sort()` returns `None`.** `x.sort()` sorts in place; `sorted(x)` returns a new list.
11. **Strings are immutable and slicing is forgiving:** `s[:12]` never throws, even for short strings.
12. **`/` is float division.** `7 / 2 == 3.5`; use `//` for integer division.
13. **Stack traces read upwards:** the last lines show where the exception happened, the first lines show the outer call.
14. **Circular imports** break startup. Put shared types low in the dependency order (this is why `models.py` exists).
15. **Python 3.9 compatibility.** This project supports 3.9, so it avoids newer syntax such as `match` statements and `X | Y` type unions outside annotations. Do not add them.
16. **`assert` is on by default.** Unlike Java, where assertions need `-ea`, Python runs them unless started with `-O`. This code uses `assert` only to state an invariant (for example in `diffview._DiffParser`), never for input validation.

---

## 14. Suggested reading order

1. [`cli.py`](../src/code_reviewer/cli.py): the entry point and the three modes (the review branch is short because [`review_flow.py`](../src/code_reviewer/review_flow.py) holds the steps the prompt shares).
2. [`engine.py`](../src/code_reviewer/engine.py): `Engine.review` and `_run_passes`; then [`review_inputs.py`](../src/code_reviewer/review_inputs.py), which builds what the Reviewer is given.
3. [`models.py`](../src/code_reviewer/models.py) and [`config.py`](../src/code_reviewer/config.py): dataclasses, your gentlest introduction to Python classes.
4. [`stop_policy.py`](../src/code_reviewer/stop_policy.py) with its test: a plain class and its rules.
5. [`passes.py`](../src/code_reviewer/passes.py) and [`roles.py`](../src/code_reviewer/roles.py): what happens in one pass and what each model is asked.
6. [`llm.py`](../src/code_reviewer/llm.py) and [`claude_cli.py`](../src/code_reviewer/claude_cli.py): how a model call is made and its output parsed.
7. [`findings.py`](../src/code_reviewer/findings.py), [`master.py`](../src/code_reviewer/master.py): validation, evidence checks and merging (the densest logic).
8. [`criteria.py`](../src/code_reviewer/criteria.py), [`diffview.py`](../src/code_reviewer/diffview.py), [`git_ops.py`](../src/code_reviewer/git_ops.py): the input side (criteria discovery, diff parsing, git/gh).
9. [`summary.py`](../src/code_reviewer/summary.py) (the shareable summary that is printed and posted), [`report.py`](../src/code_reviewer/report.py) (the detailed report), [`repl.py`](../src/code_reviewer/repl.py) and [`review_flow.py`](../src/code_reviewer/review_flow.py) (the review steps both frontends share): the output side.
10. `tests/`: executable documentation, starting with `test_engine.py`.

Small supporting modules to read when you need them: [`repl_help.py`](../src/code_reviewer/repl_help.py) (the prompt's banner and help text), [`review_options.py`](../src/code_reviewer/review_options.py) (the `review` options, shared by both frontends, and the one-shot banner), [`dry_run.py`](../src/code_reviewer/dry_run.py) (`--dry-run`), [`redact.py`](../src/code_reviewer/redact.py) (email redaction) and [`replay.py`](../src/code_reviewer/replay.py) (a developer tool for evaluating the comparator).

The prompt files in [`../prompts/`](../prompts/README.md) are plain Markdown and JSON, not code, but they are half of what the agent does.

---

## 15. Running, experimenting and debugging

```bash
# Run the tests
python3 -m unittest discover -s tests -t .
python3 -m unittest tests.test_stop_policy -v        # one test module, verbose

# Try something interactively (the Python REPL, like jshell)
PYTHONPATH=src python3
>>> from code_reviewer.criteria import slugify
>>> slugify("Retry policy")
'retry-policy'

# Run one function from the shell
PYTHONPATH=src python3 -c "from code_reviewer.config import Config; print(Config().max_passes)"
```

- **Debugger:** put `breakpoint()` on any line; running the code (including a test) drops into `pdb`, the command-line debugger (`n` next, `s` step, `c` continue, `p expr` print, `q` quit). IDEs such as PyCharm and VS Code attach their own debuggers to the same code.
- **Inspecting values:** `print(repr(x))`, or `dir(obj)` to list an object's attributes.
- **Where output goes:** each review writes full details to its run directory (see the README's "Debugging a review" table); that is the equivalent of a log file.
- **Editing safely:** change code, rerun the tests, and for prompt or criteria edits also run a one-pass review (`reviewer review <url> --passes 1`).

---

## 16. Java to Python cheat sheet

| Java | Python |
|---|---|
| `public static void main(String[] a)` | `def main(argv=None)` plus `if __name__ == "__main__":` |
| `class A extends B` | `class A(B):` |
| `class A implements I` | no interfaces; just define the methods (duck typing) |
| `new Foo(x)` | `Foo(x)` |
| `this.x` | `self.x` (explicit `self` parameter) |
| `private` / `protected` | `_name` convention |
| `static` method / field | module-level function / constant, or `@staticmethod` |
| `final` | convention (`UPPER_CASE`); `@dataclass(frozen=True)` for immutable objects |
| `record P(int a, String b)` | `@dataclass class P: a: int; b: str` |
| `enum` | string constants in this project (or `enum.Enum`) |
| `null` | `None` |
| `Optional<T>` / `@Nullable T` | `Optional[T]` (value or `None`) |
| `List<String>` / `Map<K,V>` / `Set<T>` | `List[str]` / `Dict[K, V]` / `Set[T]` |
| `Object` | `Any` |
| `String.format("%s", x)` | `"%s" % x` |
| `a ? b : c` | `b if a else c` |
| `x instanceof Foo` | `isinstance(x, Foo)` |
| `for (X x : xs)` | `for x in xs:` |
| `xs.stream().filter(p).map(f).toList()` | `[f(x) for x in xs if p(x)]` |
| `map.getOrDefault(k, d)` | `mapping.get(k, d)` |
| `map.computeIfAbsent(k, f)` | `mapping.setdefault(k, value)` |
| `x -> x + 1` | `lambda x: x + 1` |
| `Function<A,B>`, `Consumer<A>` | `Callable[[A], B]`, `Callable[[A], None]` |
| `interface` with one method | any function, or an object with `__call__` |
| `try { } catch (A \| B e) { } finally { }` | `try: ... except (A, B) as e: ... finally: ...` |
| `throw new X(msg, cause)` | `raise X(msg) from cause` |
| `throws X` / checked exceptions | none |
| try-with-resources | `with open(...) as f:` |
| `Path.of(a).resolve(b)` | `Path(a) / b` |
| `ProcessBuilder` | `subprocess.run([...], capture_output=True, text=True)` |
| Jackson `readValue` | `json.loads(text)` |
| `Pattern.compile` / `Matcher` | `re.compile(...)` / `pattern.match(text)` |
| JUnit `@Test` | `unittest` method named `test_...` |
| `System.exit(1)` | `sys.exit(1)` |
| `System.getenv("X")` | `os.environ.get("X")` |
| `pom.xml` | `pyproject.toml` |
| `mvn test` | `python3 -m unittest discover -s tests -t .` |
| classpath | `sys.path` |
| `toString()` | `__str__` |
| getter | `@property` |


## Local evals

`python -m code_reviewer.evals` is a second frontend over `Engine`, for pinned local commit pairs instead of GitHub PR metadata. `eval_cases.py` validates the suite and builds `PRContext`; only neutral title/description and the pinned diff/head reach the Reviewer, while expected answers remain evaluator-only. `eval_runner.py` runs a model/pass/repetition matrix with fixed pass attempts and default criteria, preserving interrupted trials and costs. Each trial gets its own temporary head checkout; passes within a trial share it but use fresh Claude sessions.

`eval_judgments.py` validates human decisions against the three grading checks. `eval_grading.py` computes precision, confirmed-known-issue recall and combined reports without a model. Pending/uncertain labels and incomplete trials prevent group quality scores. `rekey` copies saved reviews under revised expectations and resets labels, avoiding paid reruns. Production cases and outputs stay outside this repository, reviewed by Claude and judged by the user locally. Follow [the eval guide](../evals/README.md) for commands and limitations.
