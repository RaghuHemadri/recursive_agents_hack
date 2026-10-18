---
name: azure-entra-users-procedure
description: Procedure for opening the Microsoft Entra ID Users blade in the Azure portal via search, handling incorrect blade landings
---
# Opening the Entra ID Users blade in Azure portal

## 1. Search for Entra ID
- From the Azure portal home screen, click the **global search bar** at the top ("Search resources, services, and docs").
- Type **"Entra ID"** (or "Azure Active Directory").
- When the dropdown appears, click the **"Entra ID"** result under **Services**.
- The Entra ID service blade opens. Verify the URL contains `/Microsoft_AAD_IAM/` and the blade title shows "Microsoft Entra ID".

## 2. Navigate to the Users blade
- Within the Entra ID service blade, locate the **Users** item in the left navigation pane (or use the search within the blade).
- Click **Users** to open the **Microsoft Entra ID - Users** blade.
- The URL should now contain `/ActiveDirectoryUsersBlade/~/userList`.
- The page displays a numbered list of users with a toolbar containing **Create**, **Refresh**, and a filter box.

## 3. If you land at the wrong blade (e.g., Key Vault, Storage, etc.)
- Identify the blade title at the top and the URL.
- **Do not retry the same URL.** Instead:
  a. Click the **X** or **Home** to return to the portal home screen.
  b. Repeat **step 1** to search for "Entra ID" again.
  c. Navigate to **Users** from the Entra ID blade as in step 2.
- Report the path you took (what you clicked, in order, and the resulting URL) if the task requires debugging.

## 4. Verification point for the verifier
- The verifier independently checks the current screen: the blade title must show "Microsoft Entra ID - Users" or similar, and the URL must contain `ActiveDirectoryUsersBlade/~/userList`.
- The toolbar should show **Create** and a filter box above the user grid.
- `ANSWER: done` if the Users blade is open and verifiable; otherwise `ANSWER: not done` and specify what blade is open instead.

---
**Usage:** When the task is "Open the list of users in Microsoft Entra ID," the analyst should follow steps 1-2. If the screen is not the Users blade after step 2 (e.g., Key Vault or another blade appears), follow step 3 to reset and retry from the portal home. This skill prevents the repeated-navigation loop by giving a clear stop-and-reset point after an incorrect blade landing.