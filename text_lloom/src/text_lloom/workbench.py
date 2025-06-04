# Concept induction session functions
# =================================================

# Imports
import time
import pandas as pd
import random
from nltk.tokenize import sent_tokenize
import os
from yaspin import yaspin
import base64
import requests

import nltk
nltk.download('punkt_tab', quiet=True)

# Local imports
if __package__ is None or __package__ == '':
    # uses current directory visibility
    from concept_induction import *
    from concept import Concept
    from llm import Model, EmbedModel, OpenAIModel, OpenAIEmbedModel
else:
    # uses current package visibility
    from .concept_induction import *
    from .concept import Concept
    from .llm import Model, EmbedModel, OpenAIModel, OpenAIEmbedModel

# WORKBENCH class ================================
class lloom:
    def __init__(
        self,
        df: pd.DataFrame,
        text_col: str,
        id_col: str = None,
        distill_model: Model = None,
        cluster_model: EmbedModel = None,
        synth_model: Model = None,
        score_model: Model = None,
        debug: bool = False,
    ):
        self.debug = debug  # Whether to run in debug mode

        # Add defaults if distill_model, etc. are not specified
        def get_environ_api_key():
            if "OPENAI_API_KEY" not in os.environ:
                raise Exception("API key not found. Please set the OPENAI_API_KEY environment variable by running: `os.environ['OPENAI_API_KEY'] = 'your_key'`")
            return os.environ.get("OPENAI_API_KEY")

        if distill_model is None:
            distill_model = OpenAIModel(
                name="gpt-4o-mini", api_key=get_environ_api_key()
            )
        if cluster_model is None:
            cluster_model = OpenAIEmbedModel(
                name="text-embedding-3-large", api_key=get_environ_api_key()
            )
        if synth_model is None:
            synth_model = OpenAIModel(
                name="gpt-4o", api_key=get_environ_api_key()
            )
        if score_model is None:
            score_model = OpenAIModel(
                name="gpt-4o-mini", api_key=get_environ_api_key()
            )

        # Assign models for each operator
        self.distill_model = distill_model
        self.cluster_model = cluster_model
        self.synth_model = synth_model
        self.score_model = score_model

        # Input data
        self.doc_id_col = id_col
        self.doc_col = text_col
        df = self.preprocess_df(df)
        self.in_df = df
        self.df_to_score = df  # Default to df for concept scoring

        # Output data
        self.saved_dfs = {}  # maps from (step_name, time_str) to df
        self.concepts = {}  # maps from concept_id to Concept 
        self.concept_history = {}  # maps from iteration number to concept dictionary
        self.results = {}  # maps from concept_id to its score_df
        self.df_filtered = None  # Current quotes df
        self.df_bullets = None  # Current bullet points df
        self.select_widget = None  # Widget for selecting concepts
        
        # Cost/Time tracking
        self.time = {}  # Stores time required for each step
        self.cost = {}  # Stores cost incurred by each step
        self.tokens = {
            "in_tokens": [],
            "out_tokens": [],
        }


    # Preprocesses input dataframe
    def preprocess_df(self, df):
        # Handle missing ID column
        if self.doc_id_col is None:
            print("No `id_col` provided. Created an ID column named 'id'.")
            df = df.copy()
            self.doc_id_col = "id"
            df[self.doc_id_col] = range(len(df))  # Create an ID column

        # Handle rows with missing values
        main_cols = [self.doc_id_col, self.doc_col]
        if df[main_cols].isnull().values.any():
            print("Missing values detected. Dropping rows with missing values.")
            df = df.copy()
            len_orig = len(df)
            df = df.dropna(subset=main_cols)
            print(f"\tOriginally: {len_orig} rows, Now: {len(df)} rows")
        
        return df

    def save(self, folder, file_name=None):
        # Saves current session to file

        # Remove widget before saving (can't be pickled)
        select_widget = self.select_widget
        self.select_widget = None  

        # Remove models before saving (can't be pickled)
        models = (self.distill_model, self.cluster_model, self.synth_model, self.score_model)
        self.distill_model = None
        self.cluster_model = None
        self.synth_model = None
        self.score_model = None

        if file_name is None:
            file_name = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime())
        cur_path = f"{folder}/{file_name}.pkl"
        with open(cur_path, "wb") as f:
            pickle.dump(self, f)
        print(f"Saved session to {cur_path}")

        # Restore widget and models after saving
        self.select_widget = select_widget
        self.distill_model, self.cluster_model, self.synth_model, self.score_model = models
    
    def get_pkl_str(self):
        # Saves current session to pickle string

        # Remove widget before saving (can't be pickled)
        select_widget = self.select_widget
        self.select_widget = None  

        # Remove models before saving (can't be pickled)
        models = (self.distill_model, self.cluster_model, self.synth_model, self.score_model)
        self.distill_model = None
        self.cluster_model = None
        self.synth_model = None
        self.score_model = None

        pkl_str = pickle.dumps(self)

        # Restore widget and models after saving
        self.select_widget = select_widget
        self.distill_model, self.cluster_model, self.synth_model, self.score_model = models
        return pkl_str

    def get_save_key(self, step_name):
        t = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime())
        k = (step_name, t)  # Key of step name and current time
        return k

    # Printed text formatting
    def bold_txt(self, s):
        # Bold text
        return f"\033[1m{s}\033[0m"

    def highlight_txt(self, s, color="yellow"):
        # Highlight text (background color)
        if color == "yellow":
            return f"\x1b[48;5;228m{s}\x1b[0m"
        elif color == "blue":
            return f"\x1b[48;5;117m{s}\x1b[0m"

    def bold_highlight_txt(self, s):
        # Both bold and highlight text
        return self.bold_txt(self.highlight_txt(s))
    
    def print_step_name(self, step_name):
        # Print step name (with blue highlighting)
        format_step_name = f"{self.highlight_txt(step_name, color='blue')}"
        print(f"\n\n{format_step_name}")

    def spinner_wrapper(self):
        # Wrapper for loading spinner
        return yaspin(text="Loading")

    # Estimate cost of generation for the given params
    def estimate_gen_cost(self, params=None, verbose=False):
        if params is None:
            params = self.auto_suggest_parameters()
            print(f"No parameters provided, so using auto-suggested parameters: {params}")
        # Conservative estimates based on empirical data
        # TODO: change to gather estimates from test cases programmatically
        est_quote_tokens = 40  # Tokens for a quote
        est_bullet_tokens = 10  # Tokens for a bullet point
        est_n_clusters = 4  # Estimate of number of clusters
        est_concept_tokens = 40  # Tokens for one generated concept JSON

        est_cost = {}
        
        model = self.distill_model
        if hasattr(model, "cost"):
            # Filter: generate filter_n_quotes for each doc
            filter_in_tokens = np.sum([model.count_tokens_fn(model, filter_prompt + doc) for doc in self.in_df[self.doc_col].tolist()])
            quotes_tokens_per_doc = params["filter_n_quotes"] * est_quote_tokens 
            filter_out_tokens = quotes_tokens_per_doc * len(self.in_df)
            est_cost["distill_filter"] = model.cost_fn(model, (filter_in_tokens, filter_out_tokens))

            # Summarize: create n_bullets for each doc
            summ_prompt_tokens = model.count_tokens_fn(model, summarize_prompt)
            summ_in_tokens = np.sum([(summ_prompt_tokens + quotes_tokens_per_doc) for _ in range(len(self.in_df))])
            bullets_tokens_per_doc = params["summ_n_bullets"] * est_bullet_tokens
            summ_out_tokens = bullets_tokens_per_doc * len(self.in_df)
            est_cost["distill_summarize"] = model.cost_fn(model, (summ_in_tokens, summ_out_tokens))
        else:
            print(f"Cost estimates not available for distill model `{model.name}`")

        model = self.cluster_model
        if hasattr(model, "cost"):
            # Cluster: embed each bullet point
            cluster_cost = model.cost[0]
            cluster_tokens = bullets_tokens_per_doc * len(self.in_df)
            est_cost["cluster"] = (cluster_tokens * cluster_cost, 0)
        else:
            print(f"Cost estimates not available for cluster model `{model.name}`")

        model = self.synth_model
        if hasattr(model, "cost"):
            # Synthesize: create n_concepts for each of the est_n_clusters
            n_bullets_per_cluster = (params["summ_n_bullets"] * len(self.in_df)) / est_n_clusters
            synth_prompt_tokens = model.count_tokens_fn(model, synthesize_prompt)
            synth_in_tokens = np.sum([(synth_prompt_tokens + (est_bullet_tokens * n_bullets_per_cluster)) for _ in range(est_n_clusters)])
            synth_out_tokens = params["synth_n_concepts"] * est_n_clusters * est_concept_tokens
            est_cost["synthesize"] = model.cost_fn(model, (synth_in_tokens, synth_out_tokens))

            # Review: pass all names and prompts
            rev_in_tokens = synth_out_tokens * 2  # For both review_remove and review_merge
            rev_out_tokens = rev_in_tokens * 0.5  # Conservatively assume half size
            est_cost["review"] = model.cost_fn(model, (rev_in_tokens, rev_out_tokens))
        else:
            print(f"Cost estimates not available for synth model `{model.name}`")
        
        if len(est_cost) > 0:
            total_cost = np.sum([c[0] + c[1] for c in est_cost.values()])
            print(f"\n\n{self.bold_txt('Estimated cost')}: ${np.round(total_cost, 2)}")
            print("**Please note that this is only an approximate cost estimate**")

            if verbose:
                print(f"\nEstimated cost breakdown:")
                for step_name, cost in est_cost.items():
                    total_cost = np.sum(cost)
                    print(f"\t{step_name}: {total_cost:0.4f}")

    # Estimate cost of scoring for the given number of concepts
    def estimate_score_cost(self, n_concepts=None, batch_size=5, get_highlights=True, verbose=False, df_to_score=None):
        if n_concepts is None:
            active_concepts = self.__get_active_concepts()
            n_concepts = len(active_concepts)
        if get_highlights:
            score_prompt = score_highlight_prompt
        else:
            score_prompt = score_no_highlight_prompt
        
        # TODO: change to gather estimates from test cases programmatically
        est_concept_tokens = 20  # Tokens for concept name + prompt
        est_score_json_tokens = 100  # Tokens for score JSON for one document
            
        model = self.score_model
        if hasattr(model, "cost"):
            score_prompt_tokens = model.count_tokens_fn(model, score_prompt)
            n_batches = math.ceil(len(df_to_score) / batch_size)
            if df_to_score is None:
                df_to_score = self.df_to_score

            all_doc_tokens = np.sum([model.count_tokens_fn(model, doc) for doc in df_to_score[self.doc_col].tolist()])  # Tokens to encode all documents
            score_in_tokens = all_doc_tokens + (n_batches * (score_prompt_tokens + est_concept_tokens))
            score_out_tokens = est_score_json_tokens * n_concepts * len(df_to_score)
            est_cost = model.cost_fn(model, (score_in_tokens, score_out_tokens))

            total_cost = np.sum(est_cost)
            print(f"\n\nScoring {n_concepts} concepts for {len(df_to_score)} documents")
            print(f"{self.bold_txt('Estimated cost')}: ${np.round(total_cost, 2)}")
            print("**Please note that this is only an approximate cost estimate**")

            if verbose:
                print(f"\nEstimated cost breakdown:")
                for step_name, cost in zip(["Input", "Output"], est_cost):
                    print(f"\t{step_name}: {cost:0.4f}")
        else:
            print(f"Cost estimates not available for score model `{model.name}`")

    def auto_suggest_parameters(self, sample_size=None, target_n_concepts=20, debug=False):
        # Suggests concept generation parameters based on rough heuristics
        # TODO: Use more sophisticated methods to suggest parameters
        if sample_size is not None:
            sample_docs = self.in_df[self.doc_col].sample(sample_size).tolist()
        else:
            sample_docs = self.in_df[self.doc_col].tolist()

        # Get number of sentences in each document
        n_sents = [len(sent_tokenize(doc)) for doc in sample_docs]
        avg_n_sents = int(np.median(n_sents))
        if debug:
            print(f"N sentences: Median={avg_n_sents}, Std={np.std(n_sents):0.2f}")
        quote_per_sent = 0.75  # Average number of quotes per sentence
        filter_n_quotes = max(1, math.ceil(avg_n_sents * quote_per_sent))

        bullet_per_quote = 0.75  # Average number of bullet points per quote
        summ_n_bullets = max(1, math.floor(filter_n_quotes * bullet_per_quote))

        est_n_clusters = 3
        synth_n_concepts = math.floor(target_n_concepts / est_n_clusters)
        params = {
            "filter_n_quotes": filter_n_quotes,
            "summ_n_bullets": summ_n_bullets,
            "synth_n_concepts": synth_n_concepts,
        }
        return params
    
    def has_cost_estimates(self):
        # Check if at least one model has cost estimates
        has_cost = False
        models = ["distill_model", "cluster_model", "synth_model", "score_model"]
        for model_name in models:
            model = getattr(self, model_name)
            if not hasattr(model, "cost"):
                print(f"Token and cost summaries not available for {model_name} `{model.name}`")
            else:
                has_cost = True
        return has_cost
    
    def summary(self, verbose=True, return_vals=False):
        # Time
        total_time = np.sum(list(self.time.values()))
        print(f"{self.bold_txt('Total time')}: {total_time:0.2f} sec ({(total_time/60):0.2f} min)")
        if verbose:
            for step_name, time in self.time.items():
                print(f"\t{step_name}: {time:0.2f} sec")

        # Cost
        if self.has_cost_estimates():
            total_cost = np.sum(list(self.cost.values()))
            print(f"\n\n{self.bold_txt('Total cost')}: ${total_cost:0.2f}")
            if verbose:
                for step_name, cost in self.cost.items():
                    print(f"\t{step_name}: ${cost:0.3f}")

            # Tokens
            in_tokens = np.sum(self.tokens["in_tokens"])
            out_tokens = np.sum(self.tokens["out_tokens"])
            total_tokens =  in_tokens + out_tokens
            print(f"\n\n{self.bold_txt('Tokens')}: total={total_tokens}, in={in_tokens}, out={out_tokens}")
        
        if return_vals:
            return total_time, total_cost, total_tokens, in_tokens, out_tokens

    def show_selected(self):
        active_concepts = self.__get_active_concepts()
        print(f"\n\n{self.bold_txt('Active concepts')} (n={len(active_concepts)}):")
        for c_id, c in active_concepts.items():
            print(f"- {self.bold_txt(c.name)}: {c.prompt}")
    
    def show_prompt(self, step_name):
        # Displays the default prompt for the specified step.
        steps_to_prompts = {
            "distill_filter": filter_prompt,
            "distill_summarize": summarize_prompt,
            "synthesize": synthesize_prompt,
        }
        if step_name in steps_to_prompts:
            return steps_to_prompts[step_name]
        else:
            raise Exception(f"Operator `{step_name}` not found. The available operators for custom prompts are: {list(steps_to_prompts.keys())}")
    
    def validate_prompt(self, step_name, prompt):
        # Validate prompt for a given step to ensure that it includes the necessary template fields.
        # Raises an exception if any required field is missing.
        prompt_reqs = {
            "distill_filter": ["ex", "n_quotes", "seeding_phrase"],
            "distill_summarize": ["ex", "n_bullets", "seeding_phrase", "n_words"],
            "synthesize": ["examples", "n_concepts_phrase", "seeding_phrase"],
        }
        reqs = prompt_reqs[step_name]
        for req in reqs:
            template_str = f"{{{req}}}"  # Check for {req} in the prompt
            if template_str not in prompt:
                raise Exception(f"Custom prompt for `{step_name}` is missing required template field: `{req}`. All required fields: {reqs}. For example, this is the default prompt template:\n{self.show_prompt(step_name)}")
        

    # HELPER FUNCTIONS ================================
    async def gen(self, seed=None, params=None, n_synth=1, custom_prompts=None, auto_review=True, debug=True):
        if params is None:
            params = self.auto_suggest_parameters(debug=debug)
            if debug:
                print(f"{self.bold_txt('Auto-suggested parameters')}: {params}")
        if custom_prompts is None:
            # Use default prompts
            custom_prompts = {
                "distill_filter": self.show_prompt("distill_filter"),
                "distill_summarize": self.show_prompt("distill_summarize"),
                "synthesize": self.show_prompt("synthesize"),
            }
        else:
            # Validate that prompts are formatted correctly
            for step_name, prompt in custom_prompts.items():
                if prompt is not None:
                    self.validate_prompt(step_name, prompt)
        
        # Run cost estimation
        self.estimate_gen_cost(params)
        
        # Confirm to proceed
        if debug:
            print(f"\n\n{self.bold_highlight_txt('Action required')}")
            user_input = input("Proceed with generation? (y/n): ")
            if user_input.lower() != "y":
                print("Cancelled generation")
                return

        # Run concept generation
        filter_n_quotes = params["filter_n_quotes"]
        if (filter_n_quotes > 1) and (custom_prompts["distill_filter"] is not None):
            step_name = "Distill-filter"
            self.print_step_name(step_name)
            with self.spinner_wrapper() as spinner:
                df_filtered = await distill_filter(
                    text_df=self.in_df, 
                    doc_col=self.doc_col,
                    doc_id_col=self.doc_id_col,
                    model=self.distill_model,
                    n_quotes=params["filter_n_quotes"],
                    prompt_template=custom_prompts["distill_filter"],
                    seed=seed,
                    sess=self,
                )
                self.df_to_score = df_filtered  # Change to use filtered df for concept scoring
                self.df_filtered = df_filtered
                spinner.text = "Done"
                spinner.ok("✅")
            if debug:
                display(df_filtered)
        else:
            # Just use original df to generate bullets
            self.df_filtered = self.in_df[[self.doc_id_col, self.doc_col]]
        
        if (custom_prompts["distill_summarize"] is not None):
            step_name = "Distill-summarize"
            self.print_step_name(step_name)
            with self.spinner_wrapper() as spinner:
                df_bullets = await distill_summarize(
                    text_df=self.df_filtered, 
                    doc_col=self.doc_col,
                    doc_id_col=self.doc_id_col,
                    model=self.distill_model,
                    n_bullets=params["summ_n_bullets"],
                    prompt_template=custom_prompts["distill_summarize"],
                    seed=seed,
                    sess=self,
                )
                self.df_bullets = df_bullets
                spinner.text = "Done"
                spinner.ok("✅")
            if debug:
                display(df_bullets)
        else:
            # Just use filtered df to generate concepts
            self.df_bullets = self.df_filtered
        
        df_cluster_in = df_bullets
        synth_doc_col = self.doc_col
        synth_n_concepts = params["synth_n_concepts"]
        concept_col_prefix = "concept"
        # Perform synthesize step n_synth times
        for i in range(n_synth):
            self.concepts = {}

            step_name = "Cluster"
            self.print_step_name(step_name)
            with self.spinner_wrapper() as spinner:
                df_cluster = await cluster(
                    text_df=df_cluster_in, 
                    doc_col=synth_doc_col,
                    doc_id_col=self.doc_id_col,
                    embed_model=self.cluster_model,
                    sess=self,
                )
                spinner.text = "Done"
                spinner.ok("✅")
            if debug:
                display(df_cluster)
            
            step_name = "Synthesize"
            self.print_step_name(step_name)
            with self.spinner_wrapper() as spinner:
                df_concepts, synth_logs = await synthesize(
                    cluster_df=df_cluster, 
                    doc_col=synth_doc_col,
                    doc_id_col=self.doc_id_col,
                    model=self.synth_model,
                    concept_col_prefix=concept_col_prefix,
                    n_concepts=synth_n_concepts,
                    pattern_phrase="unique topic",
                    prompt_template=custom_prompts["synthesize"],
                    seed=seed,
                    sess=self,
                    return_logs=True,
                )
                spinner.text = "Done"
                spinner.ok("✅")
            if debug:
                print(synth_logs)

                # Review current concepts (remove low-quality, merge similar)
                if auto_review:
                    step_name = "Review"
                    self.print_step_name(step_name)
                    with self.spinner_wrapper() as spinner:
                        _, df_concepts, review_logs = await review(
                            concepts=self.concepts, 
                            concept_df=df_concepts, 
                            concept_col_prefix=concept_col_prefix, 
                            model=self.synth_model, 
                            seed=seed,
                            sess=self,
                            return_logs=True,
                        )
                        spinner.text = "Done"
                        spinner.ok("✅")
                    if debug:
                        print(review_logs)

                self.concept_history[i] = self.concepts
                if debug:
                    # Print results
                    print(f"\n\n{self.highlight_txt('Synthesize', color='blue')} {i + 1}: (n={len(self.concepts)} concepts)")
                    for k, c in self.concepts.items():
                        print(f'- Concept {k}:\n\t{c.name}\n\t- Prompt: {c.prompt}')
            
            # Update synthesize params for next iteration
            df_concepts["synth_doc_col"] = df_concepts[f"{concept_col_prefix}_name"] + ": " + df_concepts[f"{concept_col_prefix}_prompt"]
            df_cluster_in = df_concepts
            synth_doc_col = "synth_doc_col"
            synth_n_concepts = math.floor(synth_n_concepts * 0.75)
        print("✅ Done with concept generation!")

    def __concepts_to_json(self):
        concept_dict = {c_id: c.to_dict() for c_id, c in self.concepts.items()}
        # Get examples from example IDs
        for c_id, c in concept_dict.items():
            ex_ids = c["example_ids"]
            in_df = self.df_filtered.copy()
            in_df[self.doc_id_col] = in_df[self.doc_id_col].astype(str)
            examples = in_df[in_df[self.doc_id_col].isin(ex_ids)][self.doc_col].tolist()
            c["examples"] = examples
        return json.dumps(concept_dict)
    
    def select(self):
        concepts_json = self.__concepts_to_json()
        w = get_select_widget(concepts_json)
        self.select_widget = w
        return w

    async def select_auto(self, max_concepts):
        # Select the best concepts up to max_concepts
        selected_concepts = await review_select(
            concepts=self.concepts, 
            max_concepts=max_concepts, 
            model=self.synth_model, 
            sess=self,
        )

        # Handle if selection failed
        if len(selected_concepts) == 0:
            concept_ids = list(self.concepts.keys())
            selected_concepts = random.sample(concept_ids, max_concepts)

        # Activate only the selected concepts
        for c_id in selected_concepts:
            if c_id in self.concepts:
                self.concepts[c_id].active = True

    def __get_active_concepts(self):
        # Update based on widget
        if self.select_widget is not None:
            widget_data = json.loads(self.select_widget.data)
            for c_id, c in self.concepts.items():
                widget_active = widget_data[c_id]["active"]
                c.active = widget_active
        return {c_id: c for c_id, c in self.concepts.items() if c.active}
    
    def __set_all_concepts_active(self):
        for c_id, c in self.concepts.items():
            c.active = True
        # Update widget
        self.select_widget = self.select()

    # Score the specified concepts
    # If c_ids is None, only score the concepts that are active
    # If score_all is True, set all concepts as active and score all concepts
    async def score(self, c_ids=None, score_all=False, batch_size=1, get_highlights=True, ignore_existing=True, df=None, debug=True):
        concepts = {}
        if score_all:
            self.__set_all_concepts_active()
        active_concepts = self.__get_active_concepts()

        # Show error message if no concepts are active or provided
        if c_ids is None and len(active_concepts) == 0:
            raise Exception("No concepts are active. Please run `l.select()` and select at least one concept, or set `score_all=True` in your `l.score()` call to score all generated concepts.")
        
        if c_ids is None:
            # Score all active concepts
            for c_id, c in active_concepts.items():
                concepts[c_id] = c
        else:
            # Score only the specified concepts
            for c_id in c_ids:
                if c_id in active_concepts:
                    concepts[c_id] = active_concepts[c_id]
        
        # Ignore concepts that already have existing results, unless df is provided
        if ignore_existing and df is None:
            concepts = {c_id: c for c_id, c in concepts.items() if c_id not in self.results}
        
        # Run cost estimation
        if df is None:
            df = self.df_to_score

        self.estimate_score_cost(n_concepts=len(concepts), batch_size=batch_size, get_highlights=get_highlights, df_to_score=df)

        # Confirm to proceed
        if debug:
            print(f"\n\n{self.bold_highlight_txt('Action required')}")
            user_input = input("Proceed with scoring? (y/n): ")
            if user_input.lower() != "y":
                print("Cancelled scoring")
                return

        # Run usual scoring; results are stored to self.results within the function
        score_df = await score_concepts(
            text_df=df, 
            text_col=self.doc_col, 
            doc_id_col=self.doc_id_col,
            concepts=concepts,
            model=self.score_model,
            batch_size=batch_size,
            get_highlights=get_highlights,
            sess=self,
            threshold=1.0,
        )
        score_df = self.__escape_unicode(score_df)

        print("✅ Done with concept scoring!")
        return score_df

    def __get_concept_from_name(self, name):
        if name == "Outlier":
            return Concept(name="Outlier", prompt=OUTLIER_CRITERIA, example_ids=[], active=True)
        for c_id, c in self.concepts.items():
            if c.name == name:
                return c
        return None

    def __escape_unicode(self, df_in):
        # Escapes unicode characters in the dataframe to avoid UnicodeEncodeError
        df = df_in.copy()

        def parse_unicode(x):
            if (x == np.nan or x is None):
                return np.nan
            elif type(x) != str:
                return x
            return x.encode('unicode-escape').decode('ascii')
        
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].apply(lambda x: parse_unicode(x))
        return df
    
    def get_score_df(self):
        active_concepts = self.__get_active_concepts()
        active_score_dfs = [self.results[c_id] for c_id in active_concepts.keys() if c_id in self.results]
        score_df = pd.concat(active_score_dfs, ignore_index=True)
        score_df = score_df.rename(columns={"doc_id": self.doc_id_col})
        score_df = self.__escape_unicode(score_df)
        return score_df

    def __get_concept_highlights(self, c, threshold=1.0, highlight_col="highlight", lim=3):
        if c.name == "Outlier":
            return []
        if c.id not in self.results:
            return []
        score_df = self.results[c.id].copy()
        score_df = score_df[score_df["score"] >= threshold]
        highlights = score_df[highlight_col].tolist()
        # shuffle highlights
        random.shuffle(highlights)
        if lim is not None:
            highlights = highlights[:lim]
        return highlights

    def __get_rep_examples(self, c):
        if c.name == "Outlier":
            return []
        if c.id not in self.results:
            return []
        df = self.df_filtered.copy()
        df[self.doc_id_col] = df[self.doc_id_col].astype(str)
        ex_ids = c.example_ids
        ex = df[df[self.doc_id_col].isin(ex_ids)][self.doc_col].tolist()
        return ex

    def __get_df_for_export(self, item_df, threshold=1.0, include_outliers=False):
        # Prepares a dataframe meant for exporting the current session results
        # Includes concept, criteria, summary, representative examples, prevalence, and highlights
        matched = item_df[(item_df.concept_score_orig >= threshold)]
        if not include_outliers:
            matched = matched[matched.concept != "Outlier"]

        df = matched.groupby(by=["id", "concept"]).count().reset_index()[["concept", self.doc_col]]
        concepts = [self.__get_concept_from_name(c_name) for c_name in df.concept.tolist()]
        df["criteria"] = [c.prompt for c in concepts]
        df["summary"] = [c.summary for c in concepts]
        df["rep_examples"] = [self.__get_rep_examples(c) for c in concepts]
        df["highlights"] = [self.__get_concept_highlights(c, threshold) for c in concepts]
        df = df.rename(columns={self.doc_col: "n_matches"})
        df["prevalence"] = np.round(df["n_matches"] / len(self.in_df), 2)
        df = df[["concept", "criteria", "summary", "rep_examples", "prevalence", "n_matches", "highlights"]]
        return df
        
    # Visualize concept induction results
    # Parameters:
    # - cols_to_show: list (additional column names to show in the tables)
    # - slice_col: str (column name with which to slice data)
    # - max_slice_bins: int (Optional: for numeric columns, the maximum number of bins to create)
    # - slice_bounds: list (Optional: for numeric columns, manual bin boundaries to use)
    # - show_highlights: bool (whether to show text highlights)
    # - norm_by: str (how to normalize scores: "concept" or "slice")
    # - export_df: bool (whether to return a dataframe for export)
    def vis(self, cols_to_show=[], slice_col=None, max_slice_bins=5, slice_bounds=None, show_highlights=True, norm_by=None, export_df=False, include_outliers=False):
        active_concepts = self.__get_active_concepts()
        score_df = self.get_score_df()

        widget, matrix_df, item_df, item_df_wide = visualize(
            in_df=self.in_df,
            score_df=score_df,
            doc_col=self.doc_col,
            doc_id_col=self.doc_id_col,
            score_col="score",
            df_filtered=self.df_filtered,
            df_bullets=self.df_bullets,
            concepts=active_concepts,
            cols_to_show=cols_to_show,
            slice_col=slice_col,
            max_slice_bins=max_slice_bins,
            slice_bounds=slice_bounds,
            show_highlights=show_highlights,
            norm_by=norm_by,
        )
        if export_df:
            return self.__get_df_for_export(item_df, include_outliers=include_outliers)
        
        return widget

    def export_df(self, include_outliers=False):
        return self.vis(export_df=True, include_outliers=include_outliers)
    
    def export_json(self, threshold=1.0):
        def format_dict(c):
            c["criteria"] = c["prompt"]
            cur_score_df = self.results[c["id"]]
            matched = cur_score_df[cur_score_df.score >= threshold]
            c["n"] = len(matched)
            # Remove unused columns
            del c["prompt"]
            del c["id"]
            del c["active"]
            del c["example_ids"]
            return c
        active_concepts = self.__get_active_concepts()
        concepts_dict = [format_dict(c.to_dict()) for c_id, c in active_concepts.items()]
        concepts_json = json.dumps(concepts_dict)
        return concepts_json

    def submit(self):
        # Submit the current session results to the database
        # Prepare pickled lloom instance
        l_pkl = self.get_pkl_str()
        l_pkl_str = base64.b64encode(l_pkl).decode('ascii')

        # Gather user inputs
        print("Thank you for using LLooM and submitting your work! We would love to hear more about your analysis.")
        print("\nPlease provide a contact email address. This will allow us to follow up to better meet your needs and/or feature your work on our site!")
        email = input("Email address: ")

        print("\nBriefly summarize the goal of your analysis: What data were you using? What questions were you trying to answer?")
        goal = input("Goal: ")

        with self.spinner_wrapper() as spinner:
            cur_data = {
                "email": email,
                "goal": goal,
                "lloom_pkl": l_pkl_str,
            }
            hdr = {"Content-Type": "application/json"}
            cur_url = "https://lloom-log-server.vercel.app"

            try:
                r = requests.post(f"{cur_url}/save", json=cur_data, headers=hdr)

                # Parse result
                r_json = json.loads(r.text)
                status = r_json["message"]
                spinner.text = f"Submission status: {status}"
                spinner.ok("✅")
            except Exception as e:
                spinner.fail("❌")
                print(f"Error: {e}")

    async def gen_auto(
        self,
        max_concepts=8,
        seed=None, params=None, n_synth=1,
        custom_prompts=None,
        debug=True
    ):
        # Runs gen(), select(), and score() all at once
        # Run generation
        await self.gen(seed=seed, params=params, n_synth=n_synth, custom_prompts=custom_prompts, auto_review=True, debug=debug)

        # Select the best concepts
        await self.select_auto(max_concepts=max_concepts)
        self.show_selected()

        # Run scoring
        score_df = await self.score(debug=debug)
        return score_df

    async def add(self, name, prompt, ex_ids=[], get_highlights=True, debug=True):
        # Add concept
        c = Concept(name=name, prompt=prompt, example_ids=ex_ids, active=True)
        self.concepts[c.id] = c

        # Update widget
        self.select_widget = self.select()

        # Run scoring
        cur_score_df = await self.score(c_ids=[c.id], get_highlights=get_highlights, debug=debug)
        
        # Store results
        self.results[c.id] = cur_score_df
    
    
    async def edit(self):
        raise NotImplementedError("Edit function not yet implemented")
