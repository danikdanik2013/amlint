---
name: New check request
about: Suggest a new semantic check for amlint
labels: enhancement
---

**What mistake should this catch?**
<!-- Describe the Alertmanager misconfiguration -->

**Why is it hard to spot manually?**

**Config example that should trigger the check**
```yaml
route:
  receiver: ...
```

**Expected output**
```
  WARN  <your message here>
  ↳ route.routes[0]  [your-check-code]
```

**Suggested severity:** ERROR / WARN / INFO
