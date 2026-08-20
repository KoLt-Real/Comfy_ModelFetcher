# Contributing

Bug reports and patches are welcome. This package downloads files onto your disk from
URLs it found in a workflow note, so two invariants below are security ones — please
read them before your first patch.

## Invariants

1. **The HuggingFace token only ever goes to HuggingFace hosts.** `hf_token.auth_headers`
   is the single place in the codebase that builds an `Authorization` header, and it is
   host-checked. URLs come from workflow notes, i.e. from an untrusted source: a note
   authored by someone else must never be able to make your token leave for their server.
   Do not add a second header-building path.
2. **"Installed" means the loader resolves the bare filename**, and that has to stay the
   exact negation of "this one needs a relink". If the two drift apart, a row can end up
   labelled "probable duplicate" with no button to resolve it — a dead end for the user.
3. **This package registers no nodes.** It adds a top-bar button and the `/cf_mf` routes,
   nothing else, and it steps aside on an import error rather than preventing ComfyUI from
   starting. Keep it that way.

`AGENTS.md` holds the rest and is the reference; every entry is there because the thing it
describes went wrong once.

## Running the tests

```bash
./tests/run_all.sh                # Python + JS
./tests/run_all.sh --strict       # what CI should run: a skipped test fails the suite
python3 tests/e2e/test_popup.py   # browser tests need playwright
```

A skipped test is not a passing test. The suite skips cleanly when `aiohttp` or
`playwright` is missing, and it now says so out loud — if you see a skip line, that part
of the code was **not** covered by your run. Use `--strict` before opening a PR.

## Style

Everything is in **English** — code, comments, docstrings, test names, log messages,
commit messages. Match the surrounding code rather than the style you would pick.
