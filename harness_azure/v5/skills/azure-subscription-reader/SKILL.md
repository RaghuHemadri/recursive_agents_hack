---
name: azure-subscription-reader
description: Procedure for reading the Azure subscription name from the Subscriptions blade in the Azure portal
---
# Reading the Azure subscription name from the Subscriptions blade

## 1. Navigate to the Subscriptions blade
- From the Azure portal home, use the global search bar at the top ("Search resources, services, and docs"): type **"Subscriptions"** and click the **"Subscriptions"** result under **Services**.
- The Subscriptions blade opens, displaying a list of all subscriptions associated with the tenant.

## 2. Identify the subscription name
- At the top of the Subscriptions blade, verify the **blade title** shows "Subscriptions" and the URL contains `/SubscriptionsBladeV2`.
- Look at the **first card/item in the subscription list** - the subscription name is displayed as the primary text on that card.
- The subscription name typically appears as a friendly name (e.g., "Visual Studio Enterprise Subscription", "Pay-As-You-Go", etc.) followed by the subscription ID in parentheses or smaller text.
- If multiple subscriptions are listed, identify the one relevant to the task (often the first/default or the one with "Active" status).

## 3. Read and report the subscription name
- Report the subscription name exactly as displayed on the first subscription card.
- Include the URL from the current blade for verification: it should be `https://portal.azure.com/#view/Microsoft_Azure_Billing/SubscriptionsBladeV2`.
- If the task asks for "the name of my Azure subscription", report the name shown on the top/subscription card, not the subscription ID.

## 4. Verification point for the verifier
- The verifier independently checks the current screen: the Subscriptions blade must be open (URL contains `SubscriptionsBladeV2`), and the first subscription card's displayed name must match the reported name.
- `ANSWER: done` if the blade is open and the subscription name on screen matches the reported name; otherwise `ANSWER: not done` and specify what differs.

---
**Usage:** When the task is "What is the name of my Azure subscription? Open the Subscriptions page and tell me," the analyst should follow steps 1-3 to read the subscription name from the Subscriptions blade, then report it. The verifier should confirm the Subscriptions blade is open and the name on screen matches before giving `ANSWER: done`.