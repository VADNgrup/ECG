GRAPH_FIELD_SEP = "<SEP>"

PROMPTS = {}

PROMPTS["DEFAULT_TUPLE_DELIMITER"] = "<|>"
PROMPTS["DEFAULT_RECORD_DELIMITER"] = "##"
PROMPTS["DEFAULT_COMPLETION_DELIMITER"] = "<|COMPLETE|>"
PROMPTS["process_tickers"] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# [VENDOR EDIT] entity_types for technical documents (upstream default was chat-benchmark-only). See VENDOR.md.
PROMPTS["DEFAULT_ENTITY_TYPES"] = ["concept", "code", "person", "organization", "location", "event",
                                   "work", "metric", "time", "group", "product", "dataset"]


PROMPTS["entity_extraction"] = """-Goal-
Given a technical/document text and a list of entity types, identify all entities of those types from the text and all relationships among the identified entities. Prioritise CONCEPTS, methods, techniques, models, algorithms, metrics, datasets and defined technical terms over incidental named people/organisations — but a person or organization explicitly named as the AUTHOR or SOURCE of a quoted definition, finding, method or result is NOT incidental: always extract them too.

-Steps-
1. Identify all entities. For each identified entity, extract the following information:
- entity_name: The entity's STANDARD canonical name as used in the source (same language as input; capitalize if English). Keep the full standard term plus any acronym (e.g. "Convolutional Neural Network (CNN)"); do not abbreviate or split it. Do NOT append generic filler words that are not part of the real name (e.g. use "Supervised Learning", NOT "Supervised Learning Algorithm"; "Gradient Descent", NOT "Gradient Descent Method").
- entity_type: STRICTLY one of the following types: [{entity_types}]. Never invent another type. "heading", "image", "table", "formula", and "code_block" are created by the document parser and MUST NOT be emitted here. Use "code" for a PUBLIC code identifier — a function, class, module, package, parameter, or API name that exists outside this text (e.g. StandardScaler, train_test_split(), pandas, n_estimators); do NOT extract a variable that only exists inside an example (e.g. svm_clf, val_error, X_train_folds). Use "concept" for any idea, technique, process, algorithm, model, or defined term (there is NO separate "method" type — algorithms/models are "concept"). Use "metric" only for a named evaluation measure (e.g. Accuracy, RMSE). Use "work" for a named publication, paper, book, report, standard, law, or other authored work. Use "dataset" only for a named dataset, corpus, benchmark dataset, or data collection.
- entity_description: Comprehensive description of the entity's definition, attributes and role, grounded in the text.
- CROSS-REFERENCE RULE: a pointer the document uses to refer to one of its own exhibits — a figure, table, equation, chapter or section designator such as "Figure 3-3", "Table 4-1", "Equation 5-2" (in any language) — is NEVER an entity. From a line beginning "[Figure]" or "[Table]", extract the concepts, quantities and relationships the exhibit SHOWS, and attach them to the surrounding concepts; never extract the exhibit's label itself.
- CODE BLOCK RULE: a fenced code block is an EXAMPLE OF the surrounding concepts. Extract the concepts it demonstrates and the public APIs it calls; do not extract every token, literal or loop variable in it.
- SPLITTING RULE: when the text gives several DISTINCT definitions or statements about the same term, each separately introduced (e.g. "here is another definition", a block quote, or an attribution like "—Name, Year"), extract EACH one as its OWN "concept" entity — never merge distinct definitions into one description. Keep the plain term as entity_name for the general/first definition; name each attributed one "<Term> (<Name>'s definition)" using the name exactly as given.
- ATTRIBUTION RULE: a person or organization explicitly named as the author/source of a quoted definition, finding, method or result is always extracted as its own "person"/"organization" entity, with a relationship (keywords such as "defines") linking them to the concept entity they authored.
Format each entity as ("entity"{tuple_delimiter}<entity_name>{tuple_delimiter}<entity_type>{tuple_delimiter}<entity_description>

2. From the entities identified in step 1, identify all pairs of (source_entity, target_entity) that are *clearly related* to each other.
For each pair of related entities, extract the following information:
- source_entity: name of the source entity, as identified in step 1
- target_entity: name of the target entity, as identified in step 1
- relationship_description: explanation as to why you think the source entity and the target entity are related to each other
- relationship_strength: a numeric score indicating strength of the relationship between the source entity and target entity
- relationship_keywords: one or more high-level key words that summarize the overarching nature of the relationship, focusing on concepts or themes rather than specific details
Format each relationship as ("relationship"{tuple_delimiter}<source_entity>{tuple_delimiter}<target_entity>{tuple_delimiter}<relationship_description>{tuple_delimiter}<relationship_keywords>{tuple_delimiter}<relationship_strength>)

3. Identify high-level key words that summarize the main concepts, themes, or topics of the entire text. These should capture the overarching ideas present in the document.
Format the content-level key words as ("content_keywords"{tuple_delimiter}<high_level_keywords>)

4. Return output in English as a single list of all the entities and relationships identified in steps 1 and 2. Use **{record_delimiter}** as the list delimiter.

5. When finished, output {completion_delimiter}

######################
-Examples-
######################
Example 1:

Entity_types: [concept, metric, code, dataset]
Text:
Gradient Descent is an optimization algorithm that iteratively adjusts a model's parameters in the direction of the negative gradient of a loss function to minimize error. The size of each step is controlled by the learning rate; if it is set too high the algorithm may diverge, and if too low, convergence is slow. Stochastic Gradient Descent (SGD) approximates the gradient from a single training example (or a small mini-batch), trading exactness for speed.
################
Output:
("entity"{tuple_delimiter}"Gradient Descent"{tuple_delimiter}"concept"{tuple_delimiter}"An optimization algorithm that iteratively adjusts model parameters along the negative gradient of a loss function to minimize error."){record_delimiter}
("entity"{tuple_delimiter}"Loss Function"{tuple_delimiter}"concept"{tuple_delimiter}"A function measuring model error that Gradient Descent seeks to minimize."){record_delimiter}
("entity"{tuple_delimiter}"Learning Rate"{tuple_delimiter}"concept"{tuple_delimiter}"A hyperparameter controlling the step size of Gradient Descent; too high diverges, too low converges slowly."){record_delimiter}
("entity"{tuple_delimiter}"Stochastic Gradient Descent (SGD)"{tuple_delimiter}"concept"{tuple_delimiter}"A variant of Gradient Descent that estimates the gradient from a single example or mini-batch, trading exactness for speed."){record_delimiter}
("relationship"{tuple_delimiter}"Gradient Descent"{tuple_delimiter}"Loss Function"{tuple_delimiter}"Gradient Descent minimizes the loss function by following its negative gradient."{tuple_delimiter}"optimization, minimization"{tuple_delimiter}9){record_delimiter}
("relationship"{tuple_delimiter}"Gradient Descent"{tuple_delimiter}"Learning Rate"{tuple_delimiter}"The learning rate sets the step size Gradient Descent takes each iteration."{tuple_delimiter}"hyperparameter, step size"{tuple_delimiter}8){record_delimiter}
("relationship"{tuple_delimiter}"Stochastic Gradient Descent (SGD)"{tuple_delimiter}"Gradient Descent"{tuple_delimiter}"SGD is a variant of Gradient Descent using sampled gradients."{tuple_delimiter}"variant, approximation"{tuple_delimiter}8){record_delimiter}
("content_keywords"{tuple_delimiter}"optimization, gradient descent, learning rate, convergence"){completion_delimiter}
#############################
Example 2:

Entity_types: [concept, metric, code, dataset]
Text:
Precision and Recall are metrics used to evaluate a classifier. Precision is the fraction of predicted positives that are correct, while Recall is the fraction of actual positives that were retrieved. The F1 score is the harmonic mean of Precision and Recall, giving a single balanced measure.
################
Output:
("entity"{tuple_delimiter}"Precision"{tuple_delimiter}"metric"{tuple_delimiter}"The fraction of predicted positive cases that are correct; a classifier evaluation metric."){record_delimiter}
("entity"{tuple_delimiter}"Recall"{tuple_delimiter}"metric"{tuple_delimiter}"The fraction of actual positive cases that the classifier retrieved."){record_delimiter}
("entity"{tuple_delimiter}"F1 Score"{tuple_delimiter}"metric"{tuple_delimiter}"The harmonic mean of Precision and Recall, giving a single balanced evaluation measure."){record_delimiter}
("relationship"{tuple_delimiter}"F1 Score"{tuple_delimiter}"Precision"{tuple_delimiter}"The F1 score is computed as the harmonic mean of Precision and Recall."{tuple_delimiter}"evaluation, harmonic mean"{tuple_delimiter}9){record_delimiter}
("relationship"{tuple_delimiter}"F1 Score"{tuple_delimiter}"Recall"{tuple_delimiter}"The F1 score is computed as the harmonic mean of Precision and Recall."{tuple_delimiter}"evaluation, harmonic mean"{tuple_delimiter}9){record_delimiter}
("content_keywords"{tuple_delimiter}"evaluation metrics, precision, recall, classification"){completion_delimiter}
#############################
Example 3:

Entity_types: [concept, person, metric, code]
Text:
Overfitting is loosely defined as a model performing well on the data it was trained on but poorly on new data. Here is a more precise definition: a model overfits when it captures noise in the training data as if it were a real pattern, so it fails to generalize.

"A model is said to overfit when it fits the training data so closely that it fails to generalize to new data."
—Priya Rao, 2015

################
Output:
("entity"{tuple_delimiter}"Overfitting"{tuple_delimiter}"concept"{tuple_delimiter}"Loosely defined as a model performing well on its training data but poorly on new data."){record_delimiter}
("entity"{tuple_delimiter}"Overfitting (precise definition)"{tuple_delimiter}"concept"{tuple_delimiter}"A model overfits when it captures noise in the training data as if it were a real pattern, so it fails to generalize."){record_delimiter}
("entity"{tuple_delimiter}"Overfitting (Priya Rao's definition)"{tuple_delimiter}"concept"{tuple_delimiter}"A model is said to overfit when it fits the training data so closely that it fails to generalize to new data."){record_delimiter}
("entity"{tuple_delimiter}"Priya Rao"{tuple_delimiter}"person"{tuple_delimiter}"Author credited with a 2015 definition of overfitting as fitting training data so closely that the model fails to generalize."){record_delimiter}
("relationship"{tuple_delimiter}"Priya Rao"{tuple_delimiter}"Overfitting (Priya Rao's definition)"{tuple_delimiter}"Priya Rao is the credited author of this definition of overfitting."{tuple_delimiter}"defines, attribution"{tuple_delimiter}9){record_delimiter}
("relationship"{tuple_delimiter}"Overfitting"{tuple_delimiter}"Overfitting (precise definition)"{tuple_delimiter}"The precise definition refines the loose definition of the same term."{tuple_delimiter}"refinement, same concept"{tuple_delimiter}7){record_delimiter}
("relationship"{tuple_delimiter}"Overfitting"{tuple_delimiter}"Overfitting (Priya Rao's definition)"{tuple_delimiter}"Priya Rao's definition restates the same term in different words."{tuple_delimiter}"refinement, same concept"{tuple_delimiter}7){record_delimiter}
("content_keywords"{tuple_delimiter}"overfitting, generalization, definitions"){completion_delimiter}
#############################
-Real Data-
######################
Entity_types: {entity_types}
Text: {input_text}
######################
Output:
"""


PROMPTS[
    "summarize_entity_descriptions"
] = """You are a helpful assistant responsible for generating a comprehensive summary of the data provided below.
Given one or two entities, and a list of descriptions, all related to the same entity or group of entities.
Please concatenate all of these into a single, comprehensive description. Make sure to include information collected from all the descriptions.
If the provided descriptions are contradictory, please resolve the contradictions and provide a single, coherent summary.
Make sure it is written in third person, and include the entity names so we the have full context.

#######
-Data-
Entities: {entity_name}
Description List: {description_list}
#######
Output:
"""

PROMPTS[
    "entiti_continue_extraction"
] = """MANY entities were missed in the last extraction.  Add them below using the same format:
"""


PROMPTS[
    "entiti_continue_extraction_mini"
] = """MANY entities were missed in the last extraction.
After summarizing with all the information previously extracted, compared to the original text, it was noticed that the following information was mainly omitted:
{omit}

Every added entity_type MUST be selected from Entity_types. Never invent another type.

Entity_types: {entity_types}


Add them below using the same format:
"""


PROMPTS["minirag_query2kwd"] = """---Role---

You are a helpful assistant tasked with identifying both answer-type and low-level keywords in the user's query.

---Goal---

Given the query, list both answer-type and low-level keywords.
answer_type_keywords focus on the type of the answer to the certain query, while low-level keywords focus on specific entities, details, or concrete terms.
The answer_type_keywords must be selected from Answer type pool.
This pool is in the form of a dictionary, where the key represents the Type you should choose from and the value represents the example samples.

---Instructions---

- Output the keywords in JSON format.
- The JSON should have three keys:
  - "answer_type_keywords" for the types of the answer. In this list, the types with the highest likelihood should be placed at the forefront. No more than 3.
  - "entities_from_query" for specific entities or details. It must be extracted from the query.
######################
-Examples-
######################
Example 1:

Query: "How does international trade influence global economic stability?"
Answer type pool: {{
 'PERSONAL LIFE': ['FAMILY TIME', 'HOME MAINTENANCE'],
 'STRATEGY': ['MARKETING PLAN', 'BUSINESS EXPANSION'],
 'SERVICE FACILITATION': ['ONLINE SUPPORT', 'CUSTOMER SERVICE TRAINING'],
 'PERSON': ['JANE DOE', 'JOHN SMITH'],
 'FOOD': ['PASTA', 'SUSHI'],
 'EMOTION': ['HAPPINESS', 'ANGER'],
 'PERSONAL EXPERIENCE': ['TRAVEL ABROAD', 'STUDYING ABROAD'],
 'INTERACTION': ['TEAM MEETING', 'NETWORKING EVENT'],
 'BEVERAGE': ['COFFEE', 'TEA'],
 'PLAN': ['ANNUAL BUDGET', 'PROJECT TIMELINE'],
 'GEO': ['NEW YORK CITY', 'SOUTH AFRICA'],
 'GEAR': ['CAMPING TENT', 'CYCLING HELMET'],
 'EMOJI': ['🎉', '🚀'],
 'BEHAVIOR': ['POSITIVE FEEDBACK', 'NEGATIVE CRITICISM'],
 'TONE': ['FORMAL', 'INFORMAL'],
 'LOCATION': ['DOWNTOWN', 'SUBURBS']
}}
################
Output:
{{
  "answer_type_keywords": ["STRATEGY","PERSONAL LIFE"],
  "entities_from_query": ["Trade agreements", "Tariffs", "Currency exchange", "Imports", "Exports"]
}}
#############################
Example 2:

Query: "When was SpaceX's first rocket launch?"
Answer type pool: {{
 'DATE AND TIME': ['2023-10-10 10:00', 'THIS AFTERNOON'],
 'ORGANIZATION': ['GLOBAL INITIATIVES CORPORATION', 'LOCAL COMMUNITY CENTER'],
 'PERSONAL LIFE': ['DAILY EXERCISE ROUTINE', 'FAMILY VACATION PLANNING'],
 'STRATEGY': ['NEW PRODUCT LAUNCH', 'YEAR-END SALES BOOST'],
 'SERVICE FACILITATION': ['REMOTE IT SUPPORT', 'ON-SITE TRAINING SESSIONS'],
 'PERSON': ['ALEXANDER HAMILTON', 'MARIA CURIE'],
 'FOOD': ['GRILLED SALMON', 'VEGETARIAN BURRITO'],
 'EMOTION': ['EXCITEMENT', 'DISAPPOINTMENT'],
 'PERSONAL EXPERIENCE': ['BIRTHDAY CELEBRATION', 'FIRST MARATHON'],
 'INTERACTION': ['OFFICE WATER COOLER CHAT', 'ONLINE FORUM DEBATE'],
 'BEVERAGE': ['ICED COFFEE', 'GREEN SMOOTHIE'],
 'PLAN': ['WEEKLY MEETING SCHEDULE', 'MONTHLY BUDGET OVERVIEW'],
 'GEO': ['MOUNT EVEREST BASE CAMP', 'THE GREAT BARRIER REEF'],
 'GEAR': ['PROFESSIONAL CAMERA EQUIPMENT', 'OUTDOOR HIKING GEAR'],
 'EMOJI': ['📅', '⏰'],
 'BEHAVIOR': ['PUNCTUALITY', 'HONESTY'],
 'TONE': ['CONFIDENTIAL', 'SATIRICAL'],
 'LOCATION': ['CENTRAL PARK', 'DOWNTOWN LIBRARY']
}}

################
Output:
{{
  "answer_type_keywords": ["DATE AND TIME", "ORGANIZATION", "PLAN"],
  "entities_from_query": ["SpaceX", "Rocket launch", "Aerospace", "Power Recovery"]

}}
#############################
Example 3:

Query: "What is the role of education in reducing poverty?"
Answer type pool: {{
 'PERSONAL LIFE': ['MANAGING WORK-LIFE BALANCE', 'HOME IMPROVEMENT PROJECTS'],
 'STRATEGY': ['MARKETING STRATEGIES FOR Q4', 'EXPANDING INTO NEW MARKETS'],
 'SERVICE FACILITATION': ['CUSTOMER SATISFACTION SURVEYS', 'STAFF RETENTION PROGRAMS'],
 'PERSON': ['ALBERT EINSTEIN', 'MARIA CALLAS'],
 'FOOD': ['PAN-FRIED STEAK', 'POACHED EGGS'],
 'EMOTION': ['OVERWHELM', 'CONTENTMENT'],
 'PERSONAL EXPERIENCE': ['LIVING ABROAD', 'STARTING A NEW JOB'],
 'INTERACTION': ['SOCIAL MEDIA ENGAGEMENT', 'PUBLIC SPEAKING'],
 'BEVERAGE': ['CAPPUCCINO', 'MATCHA LATTE'],
 'PLAN': ['ANNUAL FITNESS GOALS', 'QUARTERLY BUSINESS REVIEW'],
 'GEO': ['THE AMAZON RAINFOREST', 'THE GRAND CANYON'],
 'GEAR': ['SURFING ESSENTIALS', 'CYCLING ACCESSORIES'],
 'EMOJI': ['💻', '📱'],
 'BEHAVIOR': ['TEAMWORK', 'LEADERSHIP'],
 'TONE': ['FORMAL MEETING', 'CASUAL CONVERSATION'],
 'LOCATION': ['URBAN CITY CENTER', 'RURAL COUNTRYSIDE']
}}

################
Output:
{{
  "answer_type_keywords": ["STRATEGY", "PERSON"],
  "entities_from_query": ["School access", "Literacy rates", "Job training", "Income inequality"]
}}
#############################
Example 4:

Query: "Where is the capital of the United States?"
Answer type pool: {{
 'ORGANIZATION': ['GREENPEACE', 'RED CROSS'],
 'PERSONAL LIFE': ['DAILY WORKOUT', 'HOME COOKING'],
 'STRATEGY': ['FINANCIAL INVESTMENT', 'BUSINESS EXPANSION'],
 'SERVICE FACILITATION': ['ONLINE SUPPORT', 'CUSTOMER SERVICE TRAINING'],
 'PERSON': ['ALBERTA SMITH', 'BENJAMIN JONES'],
 'FOOD': ['PASTA CARBONARA', 'SUSHI PLATTER'],
 'EMOTION': ['HAPPINESS', 'SADNESS'],
 'PERSONAL EXPERIENCE': ['TRAVEL ADVENTURE', 'BOOK CLUB'],
 'INTERACTION': ['TEAM BUILDING', 'NETWORKING MEETUP'],
 'BEVERAGE': ['LATTE', 'GREEN TEA'],
 'PLAN': ['WEIGHT LOSS', 'CAREER DEVELOPMENT'],
 'GEO': ['PARIS', 'NEW YORK'],
 'GEAR': ['CAMERA', 'HEADPHONES'],
 'EMOJI': ['🏢', '🌍'],
 'BEHAVIOR': ['POSITIVE THINKING', 'STRESS MANAGEMENT'],
 'TONE': ['FRIENDLY', 'PROFESSIONAL'],
 'LOCATION': ['DOWNTOWN', 'SUBURBS']
}}
################
Output:
{{
  "answer_type_keywords": ["LOCATION"],
  "entities_from_query": ["capital of the United States", "Washington", "New York"]
}}
#############################

-Real Data-
######################
Query: {query}
Answer type pool:{TYPE_POOL}
######################
Output:

"""


PROMPTS[
    "entiti_if_loop_extraction"
] = """It appears some entities may have still been missed.  Answer YES | NO if there are still entities that need to be added.
"""

PROMPTS["fail_response"] = "Sorry, I'm not able to provide an answer to that question."

PROMPTS["rag_response"] = """---Role---

You are a helpful assistant responding to questions about data in the tables provided.


---Goal---

Generate a response of the target length and format that responds to the user's question, summarizing all information in the input data tables appropriate for the response length and format, and incorporating any relevant general knowledge.
If you don't know the answer, just say so. Do not make anything up.
Do not include information where the supporting evidence for it is not provided.

---Target response length and format---

{response_type}

---Data tables---

{context_data}

Add sections and commentary to the response as appropriate for the length and format. Style the response in markdown.
"""

PROMPTS["keywords_extraction"] = """---Role---

You are a helpful assistant tasked with identifying both high-level and low-level keywords in the user's query.

---Goal---

Given the query, list both high-level and low-level keywords. High-level keywords focus on overarching concepts or themes, while low-level keywords focus on specific entities, details, or concrete terms.

---Instructions---

- Output the keywords in JSON format.
- The JSON should have two keys:
  - "high_level_keywords" for overarching concepts or themes.
  - "low_level_keywords" for specific entities or details.

######################
-Examples-
######################
Example 1:

Query: "How does international trade influence global economic stability?"
################
Output:
{{
  "high_level_keywords": ["International trade", "Global economic stability", "Economic impact"],
  "low_level_keywords": ["Trade agreements", "Tariffs", "Currency exchange", "Imports", "Exports"]
}}
#############################
Example 2:

Query: "What are the environmental consequences of deforestation on biodiversity?"
################
Output:
{{
  "high_level_keywords": ["Environmental consequences", "Deforestation", "Biodiversity loss"],
  "low_level_keywords": ["Species extinction", "Habitat destruction", "Carbon emissions", "Rainforest", "Ecosystem"]
}}
#############################
Example 3:

Query: "What is the role of education in reducing poverty?"
################
Output:
{{
  "high_level_keywords": ["Education", "Poverty reduction", "Socioeconomic development"],
  "low_level_keywords": ["School access", "Literacy rates", "Job training", "Income inequality"]
}}
#############################
-Real Data-
######################
Query: {query}
######################
Output:

"""

PROMPTS["naive_rag_response"] = """---Role---

You are a helpful assistant responding to questions about documents provided.


---Goal---

Generate a response of the target length and format that responds to the user's question, summarizing all information in the input data tables appropriate for the response length and format, and incorporating any relevant general knowledge.
If you don't know the answer, just say so. Do not make anything up.
Do not include information where the supporting evidence for it is not provided.

---Target response length and format---

{response_type}

---Documents---

{content_data}

Add sections and commentary to the response as appropriate for the length and format. Style the response in markdown.
"""
