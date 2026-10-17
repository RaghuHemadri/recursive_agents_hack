---
name: azure-vm-wizard
description: Procedure for filling the Azure VM "Create virtual machine" Basics tab, including creating a new resource group and handling the policy approval dialog.
---
# Azure VM creation – Basics tab wizard

## 1. Open the VM creation blade
- From the Azure portal home, use the global search bar: type **“Create a virtual machine”** and click the **“Create a virtual machine”** result under **Services**.
- The “Create virtual machine” blade opens on the **Basics** tab.

## 2. Create a new resource group
- In the **Resource group** field, click **“Create new”** (or the “+” button) to open the “Create new resource group” dialog.
- Enter the desired resource group name (e.g. `gh-vm-rg`).
- Click **“OK”** to create the resource group and return to the Basics tab. The resource group field now shows the new name.

## 3. Handle the policy approval dialog
- After clicking **“OK”**, a policy check may block the click if the action is deemed destructive or submit‑changing. The system will return:
  `DENIED by policy: click 'OK' is destructive or submits a change and needs explicit human approval.`
- **Do not retry the click automatically.** Instead:
  - Pause and verify with the human operator whether the resource group creation should proceed.
  - If approved, click **“OK”** again (the policy gate may need to be cleared by the human).
  - If not approved, cancel the dialog and reconsider the resource group name or task scope.

## 4. Fill the remaining Basics fields
- **Virtual machine name**: enter the specified name (e.g. `gh-vm-01`).
- **Region**: select the required region (e.g. **East US**).
- **Image**: choose the specified OS image (e.g. **Ubuntu Server 24.04 LTS**).
- **Size**: select the specified VM size (e.g. **Standard_B1s**).
- **Authentication type**: choose **SSH public key**.
- **Username**: enter the specified username (e.g. `azureuser`).

## 5. Proceed to subsequent tabs
- After the Basics tab is complete, click **“Next: Disks”** (or the appropriate navigation button) to move to the Disks tab.
- Follow the task instructions for OS disk type, Tags tab, and finally **Review + create**.
- On **Review + create**, read the summary back to the human. **Do NOT click “Create.”**

---
**Usage:** When the analyst encounters any Azure VM "Basics tab" wizard that requires creating a new resource group, they should follow this procedure step‑by‑step rather than guessing or repeating the same failed clicks. The skill is especially useful for the resource‑group‑creation + policy‑approval sequence that currently causes repeated analyst calls.