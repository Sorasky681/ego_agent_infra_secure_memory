# Alibaba Cloud official Skill profile

The competition recommends official cloud Skills. This repository selects the official
read-only `alibabacloud-sls-query` Skill for querying a configured SLS log store during
trace/evidence investigation, plus `alibabacloud-find-skills` for discovery.

Install commands from the official portal are recorded in `official-skills.lock`.
Installation alone is not a verified integration. A submission may mark SLS as verified
only after a credential-scoped, read-only query returns a trace linked to a demo task and
the redaction test passes. Without Aliyun credentials, the deterministic local trace
store is used and the UI states that the official Skill is `not_configured`.

No cloud access key is committed. Prefer short-lived credentials or a gateway consumer
token; grant query-only access to the selected project/logstore.

Official portal: <https://skills.aliyun.com/>

