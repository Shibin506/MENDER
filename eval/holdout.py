"""Held-out cases, written after the extractor and classifier were tuned on cases.py.

These were scored once and the numbers are reported as-is in the README. Do not
tune the heuristics against this file; add new tuning cases to cases.py instead.
"""

HOLDOUT = [
    {
        "id": "vitest-fail",
        "stack": "typescript",
        "category": "test_failure",
        "culprit": "src/lib/slugify.ts",
        "must_see": "AssertionError: expected 'hello--world' to be 'hello-world'",
        "failure": """\
 ✓ src/lib/money.test.ts (6 tests) 4ms
 ❯ src/lib/slugify.test.ts (3 tests | 1 failed) 7ms
   × slugify > collapses repeated separators 3ms
     → expected 'hello--world' to be 'hello-world'

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/lib/slugify.test.ts > slugify > collapses repeated separators
AssertionError: expected 'hello--world' to be 'hello-world' // Object.is equality
 ❯ src/lib/slugify.test.ts:14:32
 ❯ src/lib/slugify.ts:6:10

 Test Files  1 failed | 9 passed (10)
      Tests  1 failed | 41 passed (42)""",
        "files": {},
    },
    {
        "id": "clippy-deny",
        "stack": "rust",
        "category": "lint",
        "culprit": "src/store.rs",
        "must_see": "clippy::needless_return",
        "failure": """\
Run cargo clippy --all-targets -- -D warnings
    Checking kvstore v0.2.0 (/home/runner/work/kvstore/kvstore)
error: unneeded `return` statement
  --> src/store.rs:112:9
   |
112|         return Ok(value);
   |         ^^^^^^^^^^^^^^^^
   |
   = note: `-D clippy::needless-return` implied by `-D warnings`
   = help: for further information visit https://rust-lang.github.io/rust-clippy/master/index.html#needless_return
   = note: `#[deny(clippy::needless_return)]` on by default
error: could not compile `kvstore` (lib) due to 1 previous error""",
        "files": {},
    },
    {
        "id": "black-check",
        "stack": "python",
        "category": "lint",
        "culprit": "app/routes/orders.py",
        "must_see": "would reformat",
        "failure": """\
Run black --check .
would reformat /home/runner/work/svc/svc/app/routes/orders.py

Oh no! 💥 💔 💥
1 file would be reformatted, 87 files would be left unchanged.""",
        "files": {},
    },
    {
        "id": "pnpm-lockfile",
        "stack": "typescript",
        "category": "dependency",
        "culprit": "pnpm-lock.yaml",
        "must_see": "ERR_PNPM_OUTDATED_LOCKFILE",
        "failure": """\
Run pnpm install --frozen-lockfile
Scope: all 4 workspace projects
 ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with "frozen-lockfile" because pnpm-lock.yaml is not up to date with packages/web/package.json

Note that in CI environments this setting is true by default. If you still need to run install in such cases, use "pnpm install --no-frozen-lockfile"

    Failure reason:
    specifiers in the lockfile ({"zod":"^3.22.0"}) don't match specs in package.json ({"zod":"^3.23.8"})""",
        "files": {},
    },
    {
        "id": "kotlin-unresolved",
        "stack": "kotlin",
        "category": "build_error",
        "culprit": "app/src/main/kotlin/com/acme/ui/ProfileScreen.kt",
        "must_see": "Unresolved reference",
        "failure": """\
> Task :app:compileDebugKotlin FAILED
e: file:///home/runner/work/acme/acme/app/src/main/kotlin/com/acme/ui/ProfileScreen.kt:57:21 Unresolved reference: avatarUrl

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:compileDebugKotlin'.
> A failure occurred while executing org.jetbrains.kotlin.compilerRunner.GradleCompilerRunnerWithWorkers$GradleKotlinCompilerWorkAction
   > Compilation error. See log for more details""",
        "files": {},
    },
    {
        "id": "apt-404",
        "stack": "any",
        "category": "infra_flake",
        "culprit": None,
        "must_see": "Failed to fetch",
        "failure": """\
Run sudo apt-get install -y libpq-dev
Err:1 http://azure.archive.ubuntu.com/ubuntu jammy-updates/main amd64 libpq5 amd64 14.11-0ubuntu0.22.04.1
  404  Not Found [IP: 52.252.75.106 80]
E: Failed to fetch http://azure.archive.ubuntu.com/ubuntu/pool/main/p/postgresql-14/libpq5_14.11-0ubuntu0.22.04.1_amd64.deb  404  Not Found [IP: 52.252.75.106 80]
E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?""",
        "files": {},
    },
    {
        "id": "git-dns",
        "stack": "any",
        "category": "infra_flake",
        "culprit": None,
        "must_see": "Could not resolve host",
        "failure": """\
Run git submodule update --init --recursive
Cloning into '/home/runner/work/app/app/vendor/protos'...
fatal: unable to access 'https://github.com/acme/protos.git/': Could not resolve host: github.com
fatal: clone of 'https://github.com/acme/protos.git' into submodule path 'vendor/protos' failed""",
        "files": {},
    },
    {
        "id": "jest-snapshot",
        "stack": "typescript",
        "category": "test_failure",
        "culprit": "src/components/PriceTag.tsx",
        "must_see": "Snapshot name: `PriceTag renders discount 1`",
        "failure": """\
FAIL src/components/PriceTag.test.tsx
  ● PriceTag › renders discount

    expect(received).toMatchSnapshot()

    Snapshot name: `PriceTag renders discount 1`

    - Snapshot  - 1
    + Received  + 1

      <span class="price">
    -   $19.99
    +   $19.990
      </span>

      at Object.<anonymous> (src/components/PriceTag.test.tsx:9:30)

 › 1 snapshot failed.""",
        "files": {},
    },
    {
        "id": "command-not-found",
        "stack": "any",
        "category": "config",
        "culprit": ".github/workflows/ci.yml",
        "must_see": "command not found",
        "failure": """\
Run make proto
protoc --go_out=. --go-grpc_out=. api/v1/*.proto
/bin/bash: line 1: protoc: command not found
make: *** [Makefile:14: proto] Error 127""",
        "files": {},
    },
    {
        "id": "playwright",
        "stack": "typescript",
        "category": "test_failure",
        "culprit": "e2e/checkout.spec.ts",
        "must_see": "toBeVisible",
        "failure": """\
Running 24 tests using 2 workers
  ✓  1 [chromium] › e2e/login.spec.ts:5:5 › logs in (2.1s)
  ✘  2 [chromium] › e2e/checkout.spec.ts:18:5 › applies coupon (5.3s)

  1) [chromium] › e2e/checkout.spec.ts:18:5 › applies coupon ──────────────────

    Error: Timed out 5000ms waiting for expect(locator).toBeVisible()

    Locator: getByText('Coupon applied')
    Expected: visible
    Received: <element(s) not found>

      27 |     await page.getByRole('button', { name: 'Apply' }).click();
    > 28 |     await expect(page.getByText('Coupon applied')).toBeVisible();
         |                                                    ^

        at /home/runner/work/shop/shop/e2e/checkout.spec.ts:28:52

  1 failed
    [chromium] › e2e/checkout.spec.ts:18:5 › applies coupon""",
        "files": {},
    },
]
