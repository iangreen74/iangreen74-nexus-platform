# Lambda Packaging Notes

## Python package `__init__.py`

When packaging a Lambda that imports from a nexus submodule, the zip
must include the **real** `__init__.py` files, not empty stubs. An empty
`__init__.py` is valid Python but won't export module-level symbols
that the handler imports via `from nexus.mechanism3 import scan_tenant`.

Wrong (produces empty file):
```bash
touch "$PKG/nexus/mechanism3/__init__.py"
```

Correct:
```bash
cp nexus/mechanism3/__init__.py "$PKG/nexus/mechanism3/__init__.py"
```

The root `nexus/__init__.py` can be empty (it's just a namespace marker).
Submodule `__init__.py` files that re-export symbols must be copied.

## Verification

After packaging, verify non-zero sizes:
```bash
unzip -l /tmp/lambda.zip | grep __init__
```

## S3-hosted code

All Lambda code is hosted in:
```
s3://nexus-platform-lambda-deploys-418295677815/lambdas/<name>.zip
```

CFN templates reference via `Code: S3Bucket + S3Key` parameters.

Update flow: package -> upload to S3 -> `aws lambda update-function-code
--s3-bucket ... --s3-key ...` -> CFN template stays authoritative.
