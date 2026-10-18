---
name: azure-storage-wizard-procedure
description: Procedure for filling the Azure Storage Account "Create storage account" wizard Basics tab, Advanced tab, Tags tab, and Review + create tab.
---

# Azure Storage Account creation – Wizard procedure

## 1. Open the create storage account blade
- From the Azure portal home, use the global search bar: type **“Create a storage account”** and click the **“Create storage account”** result under **Services**.
- The “Create storage account” blade opens on the **Basics** tab.

## 2. Basics tab – Fill required fields
- **Subscription**: select the desired subscription (default is usually the active one).
- **Resource group**: click **“Create new”** (or the “+” button) to open the “Create new resource group” dialog.
  - Enter the desired resource group name (e.g. `gh-st-rg`).
  - Click **“OK”** to create the resource group and return to the Basics tab. The resource group field now shows the new name.
- **Storage account name**: enter a globally unique name (e.g. `ghstore20261017`). The name must be 3–24 lowercase letters and numbers.
- **Region**: select the required region (e.g. **East US**).
- **Performance**: select **Standard**.
- **Redundancy**: select **Locally-redundant storage (LRS)**.

## 3. Advanced tab – Configure optional settings
- **Access tier**: select **Cool** (or the access tier specified by the task). This controls how frequently the data is accessed and the associated storage costs.
- Leave other Advanced settings at their defaults unless the task requires otherwise.

## 4. Tags tab – Add tags
- Click the **Tags** tab.
- Click **“+ Add tag”** (or the “Add” button) to open the tag entry field.
- Enter the tag **Key** (e.g. `env`) and **Value** (e.g. `demo`).
- Repeat for additional tags as needed. Click **“Done”** or outside the dialog to save the tags.

## 5. Review + create tab – Verify and finish
- Click **“Next: Review + create”** (or the appropriate navigation button) to proceed to the Review + create tab.
- The portal validates the inputs and displays a summary of the storage account configuration.
- **Read the summary back to the human**, confirming all values match the task requirements (resource group name, storage account name, region, performance, redundancy, access tier, tags).
- **Do NOT click “Create.”** Wait for the human to decide whether to proceed.

---
**Usage:** When the analyst encounters the Azure Storage Account "Create storage account" wizard (Basics, Advanced, Tags tabs), they should follow this procedure step‑by‑step rather than guessing or repeating the same failed clicks. This skill captures the exact sequence of field fills and tab navigations that the team has identified as the correct flow, reducing escalations and repeated calls.