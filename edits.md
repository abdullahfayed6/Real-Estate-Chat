Update the property recommendation logic.



Current behavior:

When no properties are found within the customer's budget in the requested neighborhood, the bot immediately suggests alternative neighborhoods.



New required behavior:



Priority 1: Keep the customer in the requested neighborhood whenever possible.



Search flow:



1. Search properties in the requested neighborhood.



2. If properties exist within the customer's budget:

   - Show matching properties normally.



3. If NO properties exist within the customer's budget BUT properties do exist in the same neighborhood above the budget:

   - DO NOT suggest other neighborhoods yet.

   - Inform the customer that properties are available in the requested neighborhood.

   - Explain that the current budget is lower than the available units.

   - Show the lowest available prices by property type if possible.

   - Ask whether the customer is willing to increase the budget.



Example:



Customer:

Neighborhood: Al Uraija

Budget: 3000 SAR



Available properties:

Studio: 3200 SAR

1 Bedroom + Living Room: 3500 SAR

2 Bedrooms + Living Room: 4500 SAR



Bot response:



"Currently there are no units in Al Uraija within your budget of 3000 SAR.



However, we do have available units in Al Uraija:



• Studio starting from 3200 SAR

• 1 Bedroom + Living Room starting from 3500 SAR

• 2 Bedrooms + Living Room starting from 4500 SAR



If you would like a unit in Al Uraija, the budget would need to be increased.



Would you like to increase the budget, or should I search for similar options in other neighborhoods?"



4. If the customer agrees to increase the budget:

   - Continue searching in the same neighborhood using the new budget.



5. If the customer refuses to increase the budget:

   - Then suggest alternative neighborhoods.

   - Show neighborhoods that have matching properties within the original budget.



Important Rules:



- Never suggest other neighborhoods before checking whether properties exist in the requested neighborhood above budget.

- Always try to keep the customer in the requested neighborhood first.

- When properties exist above budget, use them as an upsell opportunity.

- Mention actual available prices from the database.

- Prefer showing the minimum available price for each property type.

- Make this behavior part of the core search workflow.