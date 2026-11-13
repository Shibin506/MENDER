"""Eval cases: the failing part of real-world-shaped CI logs, plus ground truth.

Each `failure` is the block a human would point at. `run_eval.py` buries it
inside a long, seeded stream of realistic runner noise (setup, installs,
passing tests, warnings with the word "error" in them) before scoring.

Ground truth per case:
  category   the class a maintainer would assign
  culprit    the file that needs to change (None when no single file does)
  must_see   a line that must survive extraction for a human to diagnose it
  files      source at the "head commit", used by --llm to dry-run patches
"""

CASES = [
    {
        "id": "pytest-assert",
        "stack": "python",
        "category": "test_failure",
        "culprit": "src/shop/pricing.py",
        "must_see": "assert 90.0 == 81.0",
        "failure": """\
============================= test session starts ==============================
collected 48 items

tests/test_cart.py ........                                              [ 16%]
tests/test_pricing.py ..F.....                                           [ 33%]
tests/test_users.py ................................                     [100%]

=================================== FAILURES ===================================
__________________________ test_member_discount_stacks _________________________

    def test_member_discount_stacks():
        cart = Cart(items=[Item("book", 100.0)], member=True, coupon=0.1)
>       assert total(cart) == 81.0
E       assert 90.0 == 81.0
E        +  where 90.0 = total(Cart(items=[Item(name='book', price=100.0)], member=True, coupon=0.1))

tests/test_pricing.py:22: AssertionError
=========================== short test summary info ============================
FAILED tests/test_pricing.py::test_member_discount_stacks - assert 90.0 == 81.0
========================= 1 failed, 47 passed in 2.31s =========================""",
        "files": {
            "src/shop/pricing.py": """\
from .cart import Cart

MEMBER_DISCOUNT = 0.1


def total(cart: Cart) -> float:
    subtotal = sum(item.price for item in cart.items)
    discount = 0.0
    if cart.member:
        discount = MEMBER_DISCOUNT
    if cart.coupon:
        discount = cart.coupon
    return round(subtotal * (1 - discount), 2)
""",
        },
    },
    {
        "id": "jest-expect",
        "stack": "typescript",
        "category": "test_failure",
        "culprit": "src/utils/formatDate.ts",
        "must_see": 'Expected: "2024-03-09"',
        "failure": """\
PASS src/components/Header.test.tsx
FAIL src/utils/formatDate.test.ts
  ● formatDate › pads single-digit days

    expect(received).toBe(expected) // Object.is equality

    Expected: "2024-03-09"
    Received: "2024-03-9"

      10 |   it("pads single-digit days", () => {
      11 |     const d = new Date(Date.UTC(2024, 2, 9));
    > 12 |     expect(formatDate(d)).toBe("2024-03-09");
         |                           ^
      13 |   });

      at Object.<anonymous> (src/utils/formatDate.test.ts:12:27)

Test Suites: 1 failed, 11 passed, 12 total
Tests:       1 failed, 86 passed, 87 total""",
        "files": {
            "src/utils/formatDate.ts": """\
export function formatDate(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate());
  return `${y}-${m}-${day}`;
}
""",
        },
    },
    {
        "id": "go-test",
        "stack": "go",
        "category": "test_failure",
        "culprit": "internal/cache/lru.go",
        "must_see": "--- FAIL: TestLRUEvictsOldest",
        "failure": """\
=== RUN   TestLRUGet
--- PASS: TestLRUGet (0.00s)
=== RUN   TestLRUEvictsOldest
    lru_test.go:31: expected key "a" to be evicted, but Get returned 1
--- FAIL: TestLRUEvictsOldest (0.00s)
FAIL
FAIL	github.com/acme/kv/internal/cache	0.012s
ok  	github.com/acme/kv/internal/server	0.231s
FAIL""",
        "files": {},
    },
    {
        "id": "rust-e0308",
        "stack": "rust",
        "category": "build_error",
        "culprit": "src/parser.rs",
        "must_see": "error[E0308]: mismatched types",
        "failure": """\
   Compiling tinyjson v0.3.1 (/home/runner/work/tinyjson/tinyjson)
error[E0308]: mismatched types
  --> src/parser.rs:47:20
   |
45 |     fn parse_number(&mut self) -> Result<f64, ParseError> {
   |                                   ----------------------- expected `Result<f64, ParseError>` because of return type
46 |         let text = self.take_while(|c| c.is_ascii_digit() || c == '.');
47 |         text.parse()
   |              ^^^^^^^ expected `Result<f64, ParseError>`, found `Result<_, ParseFloatError>`
   |
help: use `?` to coerce and return an appropriate `Err`, and wrap the resulting value in `Ok`
   |
47 |         Ok(text.parse()?)
   |         +++            ++

For more information about this error, try `rustc --explain E0308`.
error: could not compile `tinyjson` (lib) due to 1 previous error""",
        "files": {},
    },
    {
        "id": "tsc-ts2339",
        "stack": "typescript",
        "category": "build_error",
        "culprit": "src/api/client.ts",
        "must_see": "error TS2339",
        "failure": """\
> web@1.4.0 typecheck
> tsc --noEmit

src/api/client.ts(34,22): error TS2339: Property 'retries' does not exist on type 'ClientOptions'.
src/api/client.ts(51,9): error TS2339: Property 'retries' does not exist on type 'ClientOptions'.

Found 2 errors in the same file, starting at: src/api/client.ts:34""",
        "files": {},
    },
    {
        "id": "eslint",
        "stack": "typescript",
        "category": "lint",
        "culprit": "src/components/Button.tsx",
        "must_see": "react-hooks/exhaustive-deps",
        "failure": """\
> web@1.4.0 lint
> eslint . --max-warnings 0

/home/runner/work/web/web/src/components/Button.tsx
  18:6  error  React Hook useEffect has a missing dependency: 'onPress'  react-hooks/exhaustive-deps
  27:7  error  'unusedRef' is assigned a value but never used         @typescript-eslint/no-unused-vars

✖ 2 problems (2 errors, 0 warnings)""",
        "files": {},
    },
    {
        "id": "ruff",
        "stack": "python",
        "category": "lint",
        "culprit": "app/main.py",
        "must_see": "F401",
        "failure": """\
Run ruff check .
app/main.py:3:8: F401 [*] `os` imported but unused
app/main.py:41:89: E501 Line too long (104 > 88)
Found 2 errors.
[*] 1 fixable with the `--fix` option.""",
        "files": {},
    },
    {
        "id": "mypy",
        "stack": "python",
        "category": "build_error",
        "culprit": "app/models.py",
        "must_see": "Incompatible return value type",
        "failure": """\
Run mypy app
app/models.py:58: error: Incompatible return value type (got "str | None", expected "str")  [return-value]
app/models.py:72: error: Argument 1 to "int" has incompatible type "str | None"; expected "str"  [arg-type]
Found 2 errors in 1 file (checked 31 source files)""",
        "files": {},
    },
    {
        "id": "npm-eresolve",
        "stack": "typescript",
        "category": "dependency",
        "culprit": None,
        "must_see": "ERESOLVE unable to resolve dependency tree",
        "failure": """\
Run npm ci
npm error code ERESOLVE
npm error ERESOLVE unable to resolve dependency tree
npm error
npm error While resolving: web@1.4.0
npm error Found: react@19.0.0
npm error node_modules/react
npm error   react@"^19.0.0" from the root project
npm error
npm error Could not resolve dependency:
npm error peer react@"^17.0.0 || ^18.0.0" from react-beautiful-dnd@13.1.1
npm error node_modules/react-beautiful-dnd
npm error   react-beautiful-dnd@"^13.1.1" from the root project""",
        "files": {},
    },
    {
        "id": "pip-resolution",
        "stack": "python",
        "category": "dependency",
        "culprit": None,
        "must_see": "ResolutionImpossible",
        "failure": """\
Run pip install -r requirements.txt
ERROR: Cannot install -r requirements.txt (line 4) and pydantic==1.10.13 because these package versions have conflicting dependencies.

The conflict is caused by:
    The user requested pydantic==1.10.13
    fastapi 0.115.0 depends on pydantic!=1.8, !=1.8.1, !=2.0.0, !=2.0.1, !=2.1.0, <3.0.0 and >=1.7.4
    sqlmodel 0.0.22 depends on pydantic>=2.0

ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/#dealing-with-dependency-conflicts""",
        "files": {},
    },
    {
        "id": "npm-econnreset",
        "stack": "typescript",
        "category": "infra_flake",
        "culprit": None,
        "must_see": "ECONNRESET",
        "failure": """\
Run npm ci
npm error code ECONNRESET
npm error network aborted
npm error network This is a problem related to network connectivity.
npm error network In most cases you are behind a proxy or have bad network settings.
npm error A complete log of this run can be found in: /home/runner/.npm/_logs/2024-05-02T10_11_12_000Z-debug-0.log""",
        "files": {},
    },
    {
        "id": "docker-ratelimit",
        "stack": "docker",
        "category": "infra_flake",
        "culprit": None,
        "must_see": "toomanyrequests",
        "failure": """\
Run docker build -t app .
#3 [internal] load metadata for docker.io/library/python:3.12-slim
#3 ERROR: failed to authorize: failed to fetch anonymous token: unexpected status: 429 Too Many Requests
------
 > [internal] load metadata for docker.io/library/python:3.12-slim:
------
ERROR: failed to solve: python:3.12-slim: failed to resolve source metadata for docker.io/library/python:3.12-slim: toomanyrequests: You have reached your pull rate limit.""",
        "files": {},
    },
    {
        "id": "oom-137",
        "stack": "typescript",
        "category": "infra_flake",
        "culprit": None,
        "must_see": "exit code 137",
        "failure": """\
Run npm run build
> web@1.4.0 build
> next build

   Creating an optimized production build ...
<--- Last few GCs --->
[2201:0x6a1e000]    91234 ms: Mark-Compact 4041.2 (4134.5) -> 4038.9 (4135.2) MB
FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory
##[error]Process completed with exit code 137.""",
        "files": {},
    },
    {
        "id": "job-timeout",
        "stack": "any",
        "category": "timeout",
        "culprit": None,
        "must_see": "exceeded the maximum execution time",
        "failure": """\
tests/integration/test_sync.py::test_full_sync_large_account
##[error]The job running on runner GitHub Actions 14 has exceeded the maximum execution time of 30 minutes.
##[error]The operation was canceled.""",
        "files": {},
    },
    {
        "id": "missing-secret",
        "stack": "any",
        "category": "config",
        "culprit": ".github/workflows/release.yml",
        "must_see": "Input required and not supplied",
        "failure": """\
Run actions/create-release@v1
##[error]Input required and not supplied: token
Check the workflow file .github/workflows/release.yml:28 and repository secrets.""",
        "files": {},
    },
    {
        "id": "go-build-undefined",
        "stack": "go",
        "category": "build_error",
        "culprit": "cmd/server/main.go",
        "must_see": "undefined: config.LoadFromEnv",
        "failure": """\
Run go build ./...
# github.com/acme/kv/cmd/server
cmd/server/main.go:22:12: undefined: config.LoadFromEnv
cmd/server/main.go:23:2: declared and not used: cfg""",
        "files": {},
    },
    {
        "id": "gradle-javac",
        "stack": "java",
        "category": "build_error",
        "culprit": "src/main/java/com/acme/orders/OrderService.java",
        "must_see": "cannot find symbol",
        "failure": """\
> Task :compileJava FAILED
[ERROR] /home/runner/work/orders/orders/src/main/java/com/acme/orders/OrderService.java:[64,25] cannot find symbol
  symbol:   method findByCustomer(java.lang.String)
  location: variable repository of type com.acme.orders.OrderRepository

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':compileJava'.
> Compilation failed; see the compiler error output for details.""",
        "files": {},
    },
    {
        "id": "pytest-collection-import",
        "stack": "python",
        "category": "dependency",
        "culprit": None,
        "must_see": "No module named 'httpx'",
        "failure": """\
============================= test session starts ==============================
collected 12 items / 1 error

==================================== ERRORS ====================================
______________________ ERROR collecting tests/test_api.py ______________________
ImportError while importing test module '/home/runner/work/svc/svc/tests/test_api.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/opt/hostedtoolcache/Python/3.12.3/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_api.py:3: in <module>
    import httpx
E   ModuleNotFoundError: No module named 'httpx'
=========================== short test summary info ============================
ERROR tests/test_api.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!!""",
        "files": {},
    },
    {
        "id": "rust-test-panic",
        "stack": "rust",
        "category": "test_failure",
        "culprit": "src/ring.rs",
        "must_see": "panicked at",
        "failure": """\
running 14 tests
test ring::tests::push_pop ... ok
test ring::tests::wraps_around ... FAILED

failures:

---- ring::tests::wraps_around stdout ----
thread 'ring::tests::wraps_around' panicked at src/ring.rs:88:9:
assertion `left == right` failed
  left: Some(4)
 right: Some(1)

failures:
    ring::tests::wraps_around

test result: FAILED. 13 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out""",
        "files": {},
    },
    {
        "id": "python-runtime-exception",
        "stack": "python",
        "category": "test_failure",
        "culprit": "app/billing/invoice.py",
        "must_see": "KeyError: 'currency'",
        "failure": """\
tests/test_invoice.py::test_render_invoice FAILED                        [ 40%]

=================================== FAILURES ===================================
_____________________________ test_render_invoice ______________________________
Traceback (most recent call last):
  File "/home/runner/work/svc/svc/tests/test_invoice.py", line 17, in test_render_invoice
    html = render(invoice)
  File "/home/runner/work/svc/svc/app/billing/invoice.py", line 42, in render
    symbol = CURRENCY_SYMBOLS[invoice.meta["currency"]]
KeyError: 'currency'
=========================== short test summary info ============================
FAILED tests/test_invoice.py::test_render_invoice - KeyError: 'currency'""",
        "files": {},
    },
]
