# GOAI initial-round submission checklist

Deadline verified on 2026-08-09: **2026-08-16 23:59 (UTC+8)**. The portal allows at
most three submissions per stage and judges the last successful submission.

## Portal fields

- [ ] Work name (required)
- [ ] Repository URL beginning with `http://` or `https://` (optional)
- [ ] Demo URL (optional)
- [ ] One ZIP attachment (required; ≤1200 MB per file, ≤3600 MB cumulative this stage)
- [ ] Team institution/role, delivery name/phone/address, and one shirt size per member

## ZIP contents

- [ ] `project-summary-zh.txt` (≤500 Chinese characters; verify in portal preview)
- [ ] final proposal PPTX and PDF
- [ ] root README, license, deployment, config, sample I/O, and test instructions
- [ ] source code for API, Web, MCP servers, Agent identities, and six Skills
- [ ] synthetic judge-replay evidence export with visible labeling
- [ ] test report and claims ledger
- [ ] optional Demo video plus transcript/captions

## Truth and safety gate

- [ ] every number in the deck links to current-run evidence;
- [ ] no metric from an unrelated legacy project is reused;
- [ ] synthetic workload/hardware metrics are visibly labeled;
- [ ] integrations say not_configured/configured/reachable/verified accurately;
- [ ] repository and ZIP contain no secrets, local passwords, private ego data, or keys;
- [ ] team name/member fields replace all placeholders;
- [ ] final ZIP is reopened and the documented one-command replay succeeds.

Official references:

- <https://www.goaihz.com/submission>
- <https://www.goaihz.com/tracks?track=infra>
- <https://oss.goaihz.com/prod/20260720/6e21b053-f18b-4857-83e2-835bd96d5434.pdf>
