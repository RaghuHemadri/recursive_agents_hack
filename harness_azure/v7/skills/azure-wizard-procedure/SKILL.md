---
name: azure-wizard-procedure
description: Procedure for filling any Azure portal creation wizard: navigation, Basics tab, subsequent tabs, tags, and Review + create
---
# Azure portal creation wizard procedure

## 1. Navigate to the creation blade
- From the Azure portal home, use the global search bar at the top ("Search resources, services, and docs"): type the service name (e.g. "Resource groups", "Virtual machine") and click the matching result under **Services**.
- The service blade opens; look for a **+ Create** or **Create** button in the toolbar and click it.
- The creation wizard blade opens on the **Basics** tab.

## 2. Fill the Basics tab
- **Resource group**: If a new resource group is needed, click **"Create new"** (or the "+" button) in the Resource group field, enter the name, and click **OK** to create it.
- **Enter all required fields** on the Basics tab: name, region, image, size, authentication type, username/credentials, etc. (follow task-specific values).
- If a **policy approval dialog** appears stating the action is destructive or submit-changing, pause and verify with the human operator. If approved, click **OK** again; if not, cancel and reconsider the resource name or task scope.

## 3. Navigate subsequent tabs
- Click **Next** (or the forward arrow) to advance to the next tab (Disks, Networking, Management, Monitoring, Advanced, Tags, etc.).
- On each tab, fill in the fields as specified by the task. Use the filter box above any grid/list to narrow options.
- If a tab has a **Tags** section, add key-value tag pairs as required: click the **Tags** tab, add each key and value, then click **Next** or **Apply** to save.

## 4. Reach Review + create
- Continue clicking **Next** until you reach the **Review + create** tab.
- Read the summary back to confirm all settings are correct. **Do NOT click "Create"** yet.

## 5. Verification and submission
- The verifier independently checks the current screen: the blade title and URL must match the expected creation blade, and all fields must reflect the intended values.
- If the verifier confirms `ANSWER: done`, submit the form by clicking **Create** (or **Review + create** → **Create**).
- If the verifier says `ANSWER: not done`, identify the missing or incorrect element, navigate back to the relevant tab, correct it, and repeat from step 3.

---
**Usage:** When the analyst encounters any Azure portal creation wizard (resource groups, VMs, etc.), follow this procedure step-by-step rather than guessing or repeating the same failed clicks. This skill generalizes the proven pattern from the rg_tags_wizard task and applies to any multi-tab creation workflow.