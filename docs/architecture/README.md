# Architecture

The editable source is
[`axonllm-ledger.drawio`](axonllm-ledger.drawio). The rendered PNG is generated
from that source:

```bash
drawio --export --format png --scale 2 \
  --output docs/architecture/axonllm-ledger.png \
  docs/architecture/axonllm-ledger.drawio
```

The diagram distinguishes the currently implemented S3 ingestion, schema
normalization, in-memory processing, dashboard deployment, recurring SPICE
refresh, and asset-bundle promotion paths. Durable distributed storage remains
a post-Beta production-hardening item.
