# Progress

Tick a box only after doing it yourself, and add the date. Interviewers may
ask about any ticked item.

## Workflow
- [ ] Ran WORKFLOW.md end to end (date: )

## Missions
- [ ] 0 Workstation set up, both test suites pass
- [ ] 1 Read the order before you touch it
- [ ] 2 Morning checks
- [ ] 3 Order over SFTP with a four-eyes QC
- [ ] 4 eSIM batch
- [ ] 5 Bad files from a carrier (drills 1 to 3)
- [ ] 6 EDI and the ERP, including the SAP IDoc
- [ ] 7 Oracle questions from the business
- [ ] 8 Kubernetes on a one-node cluster
- [ ] 9 Incidents (drills 4 to 6)
- [ ] 10 Two orders at the same moment (drill 9)
- [ ] 11 Splunk alert fired on its own (drill 8)
- [ ] 12 Improved one thing through a merged pull request
- [ ] 13 Handover note

## simgen
- [ ] Ran every sample and explained each error code
- [ ] Exercise 1: explained the input and output file line by line
- [ ] Exercise 2: computed a Luhn check digit by hand
- [ ] Exercise 3: added `Profile_Version`
- [ ] Exercise 4: release sample mode
- [ ] Exercise 5: PUK repetition QC rule
- [ ] Exercise 6: broke the range lock, watched the test fail, explained why
- [ ] Exercise 7: worked through the Oracle SQL practice and the db.py differences
- [ ] Exercise 8: timed 100,000 cards and wrote a capacity estimate

## edi-bridge
- [ ] Read every sample with `pretty` and explained each segment
- [ ] Exercise 1: 810 invoice
- [ ] Exercise 3: element-level date errors
- [ ] Exercise 4: onboarded a new trading partner
- [ ] Exercise 8: explained an outbound error to a partner's EDI team
- [ ] Exercise 9: mapped the ship-to name into the SAP IDoc
- [ ] (stretch) Exercises 2, 5, 6, 7

## ops-stack
- [ ] Compose stack up; order sent and fetched over SFTP
- [ ] Stack run on Oracle (`compose.oracle.yaml`); all practice queries run
- [ ] Scaled to 3 workers and proved each order ran once
- [ ] Splunk dashboard imported; alert built and fired
- [ ] Kubernetes: ran the manifests on a one-node cluster, scaled, killed a pod
- [ ] Exercise 1: images pinned by digest
- [ ] Exercise 2: Postgres backup and restore
- [ ] Exercise 3: worker liveness probe
- [ ] Exercise 4: "no orders completed" alert

## drills
- [ ] 1 Truncated upload
- [ ] 2 Range overlap
- [ ] 3 Unknown transport key
- [ ] 4 Duplicate order
- [ ] 5 Worker lost mid-order
- [ ] 6 Database outage
- [ ] 7 EDI 997 rejection
- [ ] 8 Monitoring catches it first
- [ ] 9 Two overlapping orders at the same moment
- [ ] Fixed PRB-0001 in code, with a test
