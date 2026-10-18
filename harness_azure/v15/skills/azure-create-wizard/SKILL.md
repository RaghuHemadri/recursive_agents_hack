---
name: azure-create-wizard
description: Fill any Azure creation wizard (VM, storage, key vault, web app, VNet, resource group) without creating it
---
1. Open the service list page, click "+ Create" (or search "Create a <service>").
2. Basics tab: Resource group -> "Create new" -> type the EXACT name from the request -> OK (OK only names it).
3. Type every name EXACTLY as requested; set Region and other dropdowns (see azure-dropdowns-forms).
4. Use the tab strip (Basics, Disks, Networking, Advanced, Monitoring, Tags...) to reach each requested setting.
5. Tags tab: type the name in the Name box, the value in the Value box; a new empty row appears for the next tag.
6. Click the "Review + create" TAB and stay there: its summary must show the requested values. NEVER click Create.
