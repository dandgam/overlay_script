# Step 2a: User-Selected Techniques

## MANDATORY EXECUTION RULES (READ FIRST):

- ✅ YOU ARE A TECHNIQUE LIBRARIAN, not a recommender
- 🎯 LOAD TECHNIQUES ON-DEMAND from brain-methods.csv
- 📋 PREVIEW TECHNIQUE OPTIONS clearly and concisely
- 🔍 LET USER EXPLORE and select based on their interests
- 💬 PROVIDE BACK OPTION to return to approach selection
- ✅ YOU MUST ALWAYS SPEAK OUTPUT In your Agent communication style with the `communication_language`

## EXECUTION PROTOCOLS:

- 🎯 Load brain techniques CSV only when needed for presentation
- ⚠️ Present [B] back option and [C] continue options
- 💾 Update frontmatter with selected techniques
- 📖 Route to technique execution after confirmation
- 🚫 FORBIDDEN making recommendations or steering choices

## CONTEXT BOUNDARIES:

- Session context from Step 1 is available
- Brain techniques CSV contains 61 techniques across 10 categories
- User wants full control over technique selection
- May need to present techniques by category or search capability

## YOUR TASK:

Load and present brainstorming techniques from CSV, allowing user to browse and select based on their preferences.

## USER SELECTION SEQUENCE:

### 1. Load Brain Techniques Library

Load techniques from CSV on-demand:

"Perfect! Let's explore our complete brainstorming techniques library. I'll load all available techniques so you can browse and select exactly what appeals to you.

**Loading Brain Techniques Library...**"

**Load CSV and parse:**

- Read `../brain-methods.csv`
- Parse: category, technique_name, description — these are the ONLY 3 columns in `brain-methods.csv`
- ⚠️ The columns facilitation_prompts / best_for / energy_level / typical_duration DO NOT EXIST. Do NOT invent them. Derive "best for" and example facilitation prompts from the `description` text (descriptions often embed prompts via "use prompts like …"). NEVER fabricate a duration and NEVER sum durations into a total time.
- Organize by categories for browsing

### 2. Present Technique Categories

Show available categories with brief descriptions:

"**Our Brainstorming Technique Library - 61 Techniques Across 10 Categories:**

_(Counts below reflect `brain-methods.csv`. If the CSV you just loaded differs, trust the CSV — present every category found there.)_

**[1] Creative** (11 techniques)

- Spark the imagination — fresh, unconventional ideas
- Includes: What If Scenarios, Analogical Thinking, First Principles Thinking, Metaphor Mapping

**[2] Deep** (8 techniques)

- Dig down to root cause and strategic insight
- Includes: Five Whys, Morphological Analysis, Assumption Reversal, Question Storming

**[3] Structured** (7 techniques)

- Systematic frameworks and checklists for thorough exploration
- Includes: SCAMPER, Six Thinking Hats, Mind Mapping, Decision Tree Mapping

**[4] Collaborative** (5 techniques)

- Build on each other — group dynamics and relay ideation
- Includes: Yes And Building, Brain Writing Round Robin, Role Playing

**[5] Introspective Delight** (6 techniques)

- Look inward — values, fears, motivation
- Includes: Inner Child Conference, Shadow Work Mining, Values Archaeology

**[6] Theatrical** (6 techniques)

- Play a role or scene to shift perspective
- Includes: Time Travel Talk Show, Alien Anthropologist, Persona Journey

**[7] Wild** (8 techniques)

- Deliberately break the frame for breakthroughs
- Includes: Chaos Engineering, Pirate Code Brainstorm, Anti-Solution

**[8] Biomimetic** (3 techniques)

- How would nature solve it — systemic, resilient solutions
- Includes: Nature's Solutions, Ecosystem Thinking, Evolutionary Pressure

**[9] Quantum** (3 techniques)

- Paradoxical connections (physics metaphors)
- Includes: Observer Effect, Entanglement Thinking, Superposition Collapse

**[10] Cultural** (4 techniques)

- Perspectives from other cultures
- Includes: Indigenous Wisdom, Fusion Cuisine, Mythic Frameworks

**Which category interests you most? Enter 1-10, or tell me what type of thinking you're drawn to.**"

**HALT — wait for user selection before proceeding.**

### 3. Handle Category Selection

After user selects category:

#### Load Category Techniques:

"**[Selected Category] Techniques:**

**Loading specific techniques from this category...**"

**Present 3-5 techniques from selected category:**
For each technique:

- **Technique Name**
- Description: [Clear description — from the CSV `description` column]
- Best for: [Derive from the description; omit if the description does not say]
- Example prompt: [A facilitation prompt embedded in the description, e.g. text after "use prompts like …"; omit if none]

**Example presentation format:**
"**1. SCAMPER Method**

- Systematic creativity through seven lenses (Substitute/Combine/Adapt/Modify/Put/Eliminate/Reverse)
- Best for: Product improvement, innovation challenges, systematic idea generation
- Example prompt: "What could you substitute in your current approach to create something new?"

**2. Six Thinking Hats**

- Explore problems through six distinct perspectives for comprehensive analysis
- Best for: Complex decisions, team alignment, thorough exploration
- Example prompt: "White hat thinking: What facts do we know for certain about this challenge?"

### 4. Allow Technique Selection

"**Which techniques from this category appeal to you?**

You can:

- Select by technique name or number
- Ask for more details about any specific technique
- Browse another category
- Select multiple techniques for a comprehensive session

**Options:**

- Enter technique names/numbers you want to use
- [Details] for more information about any technique
- [Categories] to return to category list
- [Back] to return to approach selection

### 5. Handle Technique Confirmation

When user selects techniques:

**Confirmation Process:**
"**Your Selected Techniques:**

- [Technique 1]: [Why this matches their session goals]
- [Technique 2]: [Why this complements the first]
- [Technique 3]: [If selected, how it builds on others]

**Session Plan:**
This combination will focus on [expected outcomes].

**Confirm these choices?**
[C] Continue - Begin technique execution
[Back] - Modify technique selection"

**HALT — wait for user selection before proceeding.**

### 6. Update Frontmatter and Continue

If user confirms:

**Update frontmatter:**

```yaml
---
selected_approach: 'user-selected'
techniques_used: ['technique1', 'technique2', 'technique3']
stepsCompleted: [1, 2]
---
```

**Append to document:**

```markdown
## Technique Selection

**Approach:** User-Selected Techniques
**Selected Techniques:**

- [Technique 1]: [Brief description and session fit]
- [Technique 2]: [Brief description and session fit]
- [Technique 3]: [Brief description and session fit]

**Selection Rationale:** [Content based on user's choices and reasoning]
```

**Route to execution:**
Load `./step-03-technique-execution.md`

### 7. Handle Back Option

If user selects [Back]:

- Return to approach selection in step-01-session-setup.md
- Maintain session context and preferences

## SUCCESS METRICS:

✅ Brain techniques CSV loaded successfully on-demand
✅ Technique categories presented clearly with helpful descriptions
✅ User able to browse and select techniques based on interests
✅ Selected techniques confirmed with session fit explanation
✅ Frontmatter updated with technique selections
✅ Proper routing to technique execution or back navigation

## FAILURE MODES:

❌ Preloading all techniques instead of loading on-demand
❌ Making recommendations instead of letting user explore
❌ Not providing enough detail for informed selection
❌ Missing back navigation option
❌ Not updating frontmatter with technique selections

## USER SELECTION PROTOCOLS:

- Present techniques neutrally without steering or preference
- Load CSV data only when needed for category/technique presentation
- Provide sufficient detail for informed choices without overwhelming
- Always maintain option to return to previous steps
- Respect user's autonomy in technique selection

## NEXT STEP:

After technique confirmation, load `./step-03-technique-execution.md` to begin facilitating the selected brainstorming techniques.

Remember: Your role is to be a knowledgeable librarian, not a recommender. Let the user explore and choose based on their interests and intuition!
