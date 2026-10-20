# Splunk for the profile generation lab

simgen writes one JSON event per line to `events.log`. Splunk reads that file
(a *monitor* input, the same way a Universal Forwarder watches log files on a
real server) with sourcetype `_json`, so every key becomes a searchable field.

## Get the data in

With the Docker stack: `docker compose --profile splunk up -d`, wait 2-3
minutes, open http://localhost:8000 and log in as `admin` with the password
from `SPLUNK_PASSWORD` (default `LabOnly-Splunk-1`). The compose file adds the
monitor input for you.

If the data does not appear: Settings > Data inputs > Files & Directories >
New, path `/var/log/simgen/events.log`, sourcetype `_json`, index `main`.

Without Docker: install Splunk Enterprise (free trial converts to the Free
licence) on your laptop and add `simgen/events.log` the same way, or upload it
once with Settings > Add Data > Upload.

## Import the dashboard

Dashboards > Create New Dashboard > Classic Dashboards > Source, then paste
`profile_generation_dashboard.xml`.

## Searches to practise (type each one, then change it)

```
# Everything simgen logged, newest first
index=main sourcetype=_json | table _time event order_id customer error_code

# What happened to one order, in order
index=main sourcetype=_json order_id="ORD-20261019-0001" | sort _time | table _time event message

# Failures by error code
index=main sourcetype=_json event IN (order_failed, order_rejected) | stats count by error_code

# Cards delivered per customer per day
index=main sourcetype=_json event=order_completed | timechart span=1d sum(quantity) by customer

# Slowest orders
index=main sourcetype=_json event=order_completed | sort - duration_ms | head 10

# Pull the range from an overlap message with a regex
index=main sourcetype=_json error_code=E_RANGE_OVERLAP
| rex field=message "ICCID (?<first>\d+)-(?<last>\d+) overlaps order (?<clash>\S+)"
| table _time order_id first last clash
```

## Alert to create

Search: `index=main sourcetype=_json event IN (order_failed, order_rejected, worker_error)`
Save As > Alert, run every 15 minutes over the last 15 minutes, trigger when
number of results > 0, action: add to Triggered Alerts. Then drop a bad file
into the inbox and watch it fire.

## Commands worth knowing

`search`, `where`, `stats`, `timechart`, `table`, `sort`, `head`, `eval`,
`rex`, `dedup`, `top`, `rare`, `lookup`, `transaction`. Splunk's free
"Intro to Splunk" and "Using Fields" e-learning courses cover all of them.
