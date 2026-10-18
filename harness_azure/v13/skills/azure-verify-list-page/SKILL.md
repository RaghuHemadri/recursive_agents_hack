---
name: azure-verify-list-page
description: Verify that the current Azure portal screen is the correct list page for a service
---
- Check the URL: it should contain the service's browse identifier, e.g., `virtualMachinesBrowse` for VMs.
- Verify that the page title or header matches the service name (e.g., "Virtual machines").
- Look for a table or list area that shows the items; if empty, it may show "No [items] to display".
- Ensure the tab for the service is active (often highlighted).
- Use `read_page` to capture visible text and confirm key phrases.
- If the URL matches and the table/list area is present, the verification passes.