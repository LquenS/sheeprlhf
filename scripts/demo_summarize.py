"""Adapted from https://huggingface.co/spaces/stabilityai/stablelm-tuned-alpha-chat/blob/main/app.py."""
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Thread
from typing import Optional

from sheeprlhf.data.base import DataProcessor
from sheeprlhf.model.casual import CasualModel
from sheeprlhf.structure.data import DataConfig
from sheeprlhf.structure.generation import GenConfig
from sheeprlhf.structure.model import ModelConfig
from sheeprlhf.utils.cache import _IS_GRADIO_AVAILABLE
from sheeprlhf.utils.data import prepare_tokenizer
from sheeprlhf.utils.hydra import instantiate_from_config

if _IS_GRADIO_AVAILABLE:
    import gradio as gr
else:
    raise ImportError("Please install the library with `pip install .[eval] option to use this demo.")
import torch
from omegaconf import DictConfig, OmegaConf
from transformers import AutoTokenizer, GenerationConfig, TextIteratorStreamer


@dataclass
class SummaryUI:  # noqa: D101
    max_size: int = field(default=2)
    concurrency_count: int = field(default=1)
    exp_cfg: DictConfig = field(init=False)
    gen_cfg: Optional[GenConfig] = field(default=GenConfig(), init=False)
    model_cfg: Optional[ModelConfig] = field(init=False)
    data_cfg: Optional[DataConfig] = field(init=False)
    model: Optional[CasualModel] = field(default=None, init=False)
    tokenizer: Optional[AutoTokenizer] = field(default=None, init=False)
    example_post: Optional[str] = field(default=None, init=False)
    checkpoint_names: Optional[list] = field(default=None, init=False)

    def load_model(self, experiment_dir: str, checkpoint_path: str):  # noqa: D102
        print("Loading model")
        try:
            self.model_cfg = ModelConfig(**self.exp_cfg.model)
            self.data_cfg = DataConfig(**self.exp_cfg.data)
            self.model = CasualModel(model_cfg=self.model_cfg)
            if checkpoint_path != "pretrained":
                model_path = os.path.join(experiment_dir, "model", checkpoint_path)
                self.model.load_checkpoint(model_path, device="cuda", model_cfg=self.model_cfg, freeze=True)
            self.model = self.model.to("cuda")
            self.model.eval()
            self.tokenizer = prepare_tokenizer(self.data_cfg.tokenizer_name)

            return gr.update(
                visible=True, value="""<h3 style="color:green;text-align:center">Model loaded successfully</h3>"""
            )
        except Exception as e:
            print(f"Model loading failed: {str(e)}")
            return gr.update(
                visible=True,
                value="""<h3 style="color:red;text-align:center;word-break: break-all;">
                Model load failed:{}{}</h3>""".format(" ", str(e)),
            )

    def load_experiment(self, experiment_dir: str):  # noqa: D102
        print("Loading experiment")
        try:
            self.exp_cfg = OmegaConf.load(Path(experiment_dir) / ".hydra/config.yaml")
            self.gen_cfg: GenConfig = GenConfig(**self.exp_cfg.generation)
            checkpoint_names = ["pretrained"] + os.listdir(os.path.join(experiment_dir, "model"))
            info = gr.update(
                visible=True, value="""<h3 style="color:green;text-align:center">Experiment loaded successfully</h3>"""
            )
            dropdown = gr.update(choices=checkpoint_names)
            return info, dropdown

        except Exception as e:
            print(f"Experiment loading failed: {str(e)}")
            return gr.update(
                visible=True,
                value="""<h3 style="color:red;text-align:center;word-break: break-all;">
                Experiment load failed:{}{}</h3>""".format(" ", str(e)),
            )

    def load_example(self):  # noqa: D102
        print("Loading example")
        data_processor: DataProcessor = instantiate_from_config(self.data_cfg)
        full_path = data_processor.full_path
        if os.path.exists(full_path):
            example_data = torch.load(os.path.join(full_path, "example_prompt.pt"))
            prompt = example_data["prompt"][:-7]
            subreddit_index = prompt.index("SUBREDDIT: ")
            title_index = prompt.index("TITLE: ")
            post_index = prompt.index("POST: ")
            subreddit = prompt[subreddit_index + 11 : title_index - 1]
            title = prompt[title_index + 7 : post_index - 1]
            post = prompt[post_index + 6 : -1]
            info = gr.update(
                visible=True, value="""<h3 style="color:green;text-align:center">Example loaded successfully</h3>"""
            )
            return info, gr.update(value=subreddit), gr.update(value=title), gr.update(value=post)
        else:
            return (
                gr.update(
                    visible=True,
                    value="""<h3 style="color:red;text-align:center;word-break: break-all;">First experiment</h3>""",
                ),
                None,
                None,
                None,
            )

    def clear(*args):  # noqa: D102
        return [None for _ in args]

    @torch.inference_mode()
    def summary(self, subreddit: str, title: str, post: str):  # noqa: D102
        prompt = f"SUBREDDIT: {subreddit} TITLE: {title} POST: {post} TL;DR: "

        # Tokenize the messages string
        model_inputs = self.tokenizer([prompt], return_tensors="pt").to("cuda")
        streamer = TextIteratorStreamer(self.tokenizer, timeout=10.0, skip_prompt=True, skip_special_tokens=True)
        generation_config = GenerationConfig.from_pretrained(self.model_cfg.repo_name, **asdict(self.gen_cfg))
        t = Thread(
            target=self.model.generate,
            kwargs={**model_inputs, **{"generation_config": generation_config, "streamer": streamer}},
        )
        t.start()

        partial_text = ""
        for new_text in streamer:
            partial_text += new_text
        return partial_text

    def launch(self):  # noqa: D102
        with gr.Blocks() as demo:
            gr.Markdown("## LLM Summarization Demo")
            with gr.Row():
                with gr.Column(scale=5):
                    with gr.Row(), gr.Column(scale=5):
                        exp_dir_input = gr.Textbox(
                            value="",
                            interactive=True,
                            placeholder="Enter the experiment directory",
                            label="Experiment Directory",
                            visible=True,
                        )
                        load_experiment_btn = gr.Button("Load Experiment")
                        load_experiment_info = gr.Markdown(visible=False, value="")
                        checkpoints = gr.Dropdown(
                            options=[],
                            label="Checkpoint",
                            visible=True,
                        )
                        load_model_btn = gr.Button("Load Model")
                        load_model_info = gr.Markdown(visible=False, value="")

                        load_experiment_btn.click(
                            self.load_experiment,
                            inputs=[exp_dir_input],
                            outputs=[load_experiment_info, checkpoints],
                            show_progress=True,
                        )
                        load_model_btn.click(
                            self.load_model,
                            inputs=[exp_dir_input, checkpoints],
                            outputs=[load_model_info],
                            show_progress=True,
                        )
                    with gr.Row(), gr.Column(scale=3):
                        self.subreddit_text = gr.Textbox(
                            value="",
                            interactive=True,
                            placeholder="r/TechSupport",
                            label="Subreddit Name",
                            visible=True,
                        )
                        self.title_text = gr.Textbox(
                            value="",
                            interactive=True,
                            placeholder="Title of the Reddit post",
                            label="Title",
                            visible=True,
                        )
                        self.post_text = gr.Textbox(
                            value="",
                            interactive=True,
                            placeholder="Body of the Reddit post",
                            label="Post",
                            visible=True,
                        )
                    with gr.Row():
                        self.summary_text = gr.Textbox(
                            value="",
                            interactive=False,
                            placeholder="",
                            label="Summary",
                            visible=True,
                        )
                    with gr.Row():
                        load_example = gr.Button("Load Example")
                        clear = gr.Button("Clear")
                        submit = gr.Button("Submit")
                        load_example_info = gr.Markdown(visible=False, value="")
                        submit.click(
                            fn=self.summary,
                            inputs=[self.subreddit_text, self.title_text, self.post_text],
                            outputs=[self.summary_text],
                            queue=False,
                        )
                        load_example.click(
                            fn=self.load_example,
                            outputs=[load_example_info, self.subreddit_text, self.title_text, self.post_text],
                            queue=False,
                        )
                        clear.click(
                            self.clear,
                            [self.subreddit_text, self.title_text, self.post_text],
                            [self.subreddit_text, self.title_text, self.post_text],
                        )

                with gr.Column(scale=1), gr.Row():
                    topk_s = gr.Slider(
                        0,
                        1000,
                        value=self.gen_cfg.top_k,
                        label="Top-k",
                        interactive=True,
                        step=1,
                    )
                    topp_s = gr.Slider(0, 1, value=self.gen_cfg.top_p, label="Top-p", interactive=True)
                    temp_s = gr.Slider(0, 1, value=self.gen_cfg.temperature, label="Temperature", interactive=True)
                    max_new_tokens_s = gr.Slider(
                        1, 128, value=self.gen_cfg.max_new_tokens, label="Max new tokens", interactive=True
                    )
                    do_sample_s = gr.Checkbox(label="Do Sample", checked=True)
                    load_from_exp_button = gr.Button("Load from Experiment", label="Load from Experiment")
                    load_from_exp_info = gr.Markdown(visible=False, value="")

                    def load_from_experiment():
                        return (
                            self.gen_cfg.top_k,
                            self.gen_cfg.top_p,
                            self.gen_cfg.temperature,
                            self.gen_cfg.max_new_tokens,
                            self.gen_cfg.do_sample,
                        )

                    load_from_exp_button.click(
                        load_from_experiment,
                        outputs=[
                            load_from_exp_info,
                            topk_s,
                            topp_s,
                            temp_s,
                            max_new_tokens_s,
                            do_sample_s,
                        ],
                        show_progress=True,
                    )

                    update_button = gr.Button("Update Settings", label="Update")
                    update_info = gr.Markdown(visible=False, value="")

                    def update_settings(topk_s, topp_s, temp_s, max_new_tokens_s, do_sample_s):
                        self.gen_cfg.top_k = topk_s
                        self.gen_cfg.top_p = topp_s
                        self.gen_cfg.temperature = temp_s
                        self.gen_cfg.max_new_tokens = max_new_tokens_s
                        self.gen_cfg.do_sample = do_sample_s
                        print(self.gen_cfg)
                        return gr.update(
                            visible=True,
                            value="""<h3 style="color:green;text-align:center">
                                Settings updated successfully</h3>""",
                        )

                    update_button.click(
                        update_settings,
                        inputs=[topk_s, topp_s, temp_s, max_new_tokens_s, do_sample_s],
                        outputs=[update_info],
                        show_progress=True,
                    )

        demo.queue(max_size=self.max_size, concurrency_count=self.concurrency_count)
        demo.launch()


if __name__ == "__main__":
    SummaryUI().launch()
