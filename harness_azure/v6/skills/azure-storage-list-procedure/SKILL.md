---
name: azure-storage-list-procedure
description: How to open the Storage accounts list blade in the Azure portal and confirm it is the current screen
---
- To open the Storage accounts list, use the global search bar at the top of the Azure portal:
  type "Storage accounts" and click the matching item under "Services".
- Alternatively, click the "Storage accounts" link in the left navigation menu under the Azure services section.
- The storage accounts list opens as a blade on the right side of the portal (StorageAccountsBrowse).
- The current screen is confirmed as the Storage accounts list when:
  * The page URL contains "StorageAccountsBrowse" or "StorageHub",
  * The breadcrumb at the top shows "Storage accounts" or "Storage center",
  * The toolbar shows a "Create" button and a filter box above the storage account grid,
  * The page may display "No storage accounts to display" if the subscription has no storage accounts (this is still the correct screen).
- Definition of DONE for "Open the list of storage accounts": the Storage accounts blade is open and visible, regardless of whether any accounts are listed. The presence of the "Create" button and filter box confirms the correct screen state.