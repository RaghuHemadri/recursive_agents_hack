---
name: azure-read-tours
description: Read values across several Azure blades (IDs, statuses, counts) and finish on the requested blade
---
- Use read_page to read values; IDs are GUIDs (8-4-4-4-12). Report each value as soon as you read it.
- Counts: list pages show "Showing 1 - N of M" or a total; Entra ID: Overview shows tenant ID + primary domain;
  Users, Groups and App registrations ("All applications" tab) list totals.
- Subscription: Overview shows subscription ID and status; Resource providers lists registration state;
  Access control (IAM) -> Role assignments tab shows the count.
- End on the blade the request names, then report all values in one answer.
