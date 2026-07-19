# Contributing

Hundred Percent Print is built for dimension-critical fabric pattern printing. Changes should fail closed when scale cannot be verified.

Before opening a pull request, run:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall src scripts tests
sh -n docker/entrypoint.sh
```

Optional local integration tests require CUPS and do not print paper:

```sh
HPP_RUN_INTEGRATION=1 PYTHONPATH=src python3 -m unittest tests.test_self_test_integration -v
HPP_RUN_CUPS_FRONTEND_INTEGRATION=1 PYTHONPATH=src python3 -m unittest tests.test_self_test_integration -v
```

For Docker changes, build the image locally:

```sh
docker build -t hundred-percent-print:test .
```

Keep new runtime behavior observable in `jobs.jsonl` when it affects accepted, rejected, or forwarded print jobs.
