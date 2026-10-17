---
name: azure-iam-role-assignments-procedure
description: Procedure for navigating to and reading Role assignments in the Azure portal Access control (IAM) page
---
# Azure portal Access control (IAM) → Role assignments procedure

## 1. Open the Access control (IAM) blade
- From the current subscription blade or home page, use the global search bar at the top ("Search resources, services, and docs"): type **"Access control (IAM)"** and click the **"Access control (IAM)"** result under **Services**.
- Alternatively, from the left navigation menu, click **“Access control (IAM)”** under the **Settings** section of the subscription blade.
- The Access control (IAM) blade opens, showing the **Overview** tab by default.

## 2. Switch to the Role assignments tab
- In the Access control (IAM) blade, locate the tab bar at the top with tabs: **Overview**, **Role assignments**, **Users**, **Groups**, **App registrations**.
- Click the **“Role assignments”** tab.
- The Role assignments page loads, displaying a list of all role assignments for the subscription.

## 3. Read the role assignments count
- At the top of the Role assignments page, look for a summary count (e.g., "Showing 1-10 of 25 role assignments" or similar).
- If a count is shown, report the total number of role assignments displayed.
- If no summary count is visible, look at the grid of role assignments; each row represents one role assignment.
- Report the number of role assignments visible on the current page, and note if pagination is available.

## 4. Verification point for the verifier
- The verifier independently checks the current screen: the Access control (IAM) blade must be open, the **Role assignments** tab must be active (selected), and the role assignments list must be visible.
- `ANSWER: done` if the Role assignments tab is open and the role assignments count/list is visible on screen; otherwise `ANSWER: not done` and specify which tab or element is missing.

---
**Usage:** When the task requires opening Access control (IAM) and reporting how many role assignments there are, the analyst should follow steps 1-3 to navigate to the Role assignments tab and read the count. The verifier should confirm the IAM blade is open with the Role assignments tab selected before giving `ANSWER: done`.