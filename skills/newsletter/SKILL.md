---
name: newsletter
description: Workflow pro BB newsletter (text -> HTML -> Ecomail)
---
Help create a newsletter design for BB.

## Context

The user creates newsletters for BB (client). Workflow:
1. User gets text content
2. Design HTML layout + create/source images
3. Put it into Ecomail
4. Set up campaign

The newsletter layouts are in `{{PROJECT_DIRS}}`.

## Instructions

1. Ask user: "Pošli mi text pro newsletter" (if not already provided via $ARGUMENTS)
2. Once text is provided:
   - Analyze the content (topic, key points, CTA)
   - Find existing newsletter templates in the project directory
   - Create/adapt HTML layout with the new content
   - Suggest image needs (hero image, section images)
   - If user wants, generate image prompts for AI generation
3. Output the final HTML ready for Ecomail paste
4. Remind user of Ecomail campaign settings if needed

## Design principles

- Mobile-first responsive
- Clean, readable typography
- Clear CTA buttons
- Brand-consistent styling (use existing CSS from templates)
- Images should be hosted or base64 (for Ecomail compatibility, prefer hosted URLs)

## File locations

- Look for existing templates in: `<project>/` or similar BB project dirs
- Save new newsletter HTML to the appropriate project folder
