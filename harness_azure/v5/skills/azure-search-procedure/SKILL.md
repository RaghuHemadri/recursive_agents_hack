---
name: azure-search-procedure
description: Procedure for searching a service in the Azure portal and verifying results appear
---
# Azure portal search procedure

## 1. Focus the search bar
- From the current Azure portal screen, click the **global search bar** at the top ("Search resources, services, and docs") to place focus on it.
- The search bar is now active and ready for input.

## 2. Type the service name
- Type the name of the service you are looking for (e.g., "Key Vault").
- As you type, the portal may show a dropdown of matching services under "Services".

## 3. Confirm search results appear
- **Press Enter** to submit the search, OR click the matching service item under "Services" in the dropdown.
- The portal will navigate to the service's blade/page. Verify the blade opened by checking:
  - The URL contains the expected service path (e.g., `/KeyVaults` for Key Vault).
  - The blade title at the top matches the service name.
  - A toolbar or grid of resources is visible (list pages have a toolbar with +Create, Refresh, etc.).

## 4. If no dropdown appears or results don't open
- Ensure you have typed enough characters for a match.
- If the dropdown shows no matches, try typing the full service name or check the spelling.
- If the blade does not open after pressing Enter, verify the search bar is still focused and try pressing Enter again, or click the "Search resources, services, and docs" bar again to refocus.

## 5. Verification point for the verifier
- The verifier independently checks the current screen: the URL must contain the expected service path, and the blade title must match the searched service name.
- `ANSWER: done` if the service blade is open and verifiable; otherwise `ANSWER: not done` and specify what is missing.

---
**Usage:** When the task is "Open the list of key vaults" or any task requiring you to find and open a service by name, follow steps 1-4. The analyst should type the service name and press Enter/click a result rather than repeatedly clicking the search bar focus. This skill prevents the "repeated call" guard by giving a clear stop point after submitting the search.