---
slug: subscription-usage-alerts
title: Subscription, usage & alerts
description: Manage commercial status, inspect consumption, set budgets, and route actionable alerts.
order: 80
read_time: 7 min
icon: chart
---

# Subscription, usage & alerts

Use [Subscription](/admin?tab=billing) for plan and payment context, [Usage](/admin?tab=usage) for tenant and per-user consumption, [Licensing](/admin?tab=licensing) for seats and premium access, and [Settings](/admin?tab=settings) for alert thresholds and recipients.

## Subscription

Confirm the tenant, current plan, billing state, renewal terms, and authorized purchaser before making a commercial change. A successful payment redirect or confirmation does not replace checking the resulting plan and seat state.

## Usage review

Compare a consistent date range and separate standard activity, premium model use, MCP calls, automation retries, and integration processing when those breakdowns are available. High usage can be legitimate; unexpected shape or timing is the stronger signal.

The per-user view supports investigation and coaching. It should not be used as a stand-alone measure of productivity or professional value.

## Budgets and alert thresholds

For plans that support budgets, assign realistic limits and define the action expected when a threshold is reached. Configure percentage thresholds in ascending order and avoid so many alerts that recipients ignore them.

Alert recipients should be monitored firm-controlled addresses or groups. Delivery may use the connected Microsoft or Google account, with SMTP as configured fallback. Test delivery after changing recipients or mail integration.

## Respond to an alert

1. confirm the tenant, period, metric, and threshold;
2. inspect user and tool-level activity;
3. look for repeated failures, loops, new integrations, or exposed keys;
4. contain access if compromise is plausible;
5. preserve request IDs and relevant audit evidence; and
6. document the outcome and any budget change.

Do not simply raise a limit until the underlying activity is understood.
