# Validation record (what worked / what failed)
- [live] screen_reading: validation worked 1x, FAILED 0x
- [live] web_navigation: validation worked 3x, FAILED 0x
- gate WORKED [skills/azure-portal/SKILL.md] Adding a explicit preference for the top search bar will guide agents to use it as the primary method for opening Azure services, making interactions faster and :: applied as v2: learned from your message: Always use the top search bar to open a service, it is faster than menus and tiles.
- gate WORKED [lead_prompt.md] Adding a check for signed-in state before asking to log in will prevent unnecessary login prompts when the user is already signed in, thus fixing the failure wh :: applied as v3: you marked the task as failed (no: the portal was already signed in, the login URL was only a redirect. Observe again before asking me to log in.)
- gate WORKED [lead_prompt.md] If the lead explicitly checks for Azure portal signed-in indicators (user email visible, Azure services list on home page) before asking the human to log in, an :: applied as v4: learned from your message: You are already signed in, the portal home is showing now. Look at the screen again and continue.
