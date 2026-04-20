"""dataset_loader.py

This module implements the DatasetLoader class which is responsible for loading,
preprocessing, and batching raw datasets for Transformer training experiments.
It supports both machine translation (e.g., WMT14_EnDe) and constituency parsing (WSJ)
tasks. The module utilizes PyTorch Dataset and DataLoader abstractions along with a custom
batch sampler based on approximate token counts per batch.

Required external packages:
    torch==1.9.0
    numpy==1.21.0
    sacrebleu==2.0.0
    tqdm==4.62.0
"""

import os
import random
from collections import Counter
from typing import Any, Dict, List, Tuple

import torch
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import Dataset, DataLoader, Sampler


# ----------------------- Helper Functions ----------------------- #
def tokenize(text: str) -> List[str]:
    """Simple whitespace tokenization."""
    return text.strip().split()


def build_vocab(tokenized_texts: List[List[str]], pad_token: str, unk_token: str, min_freq: int = 1) -> Dict[str, int]:
    """
    Build a vocabulary mapping from tokens to indices.
    Starts with pad_token at index 0 and unk_token at index 1.
    Tokens with frequency >= min_freq are added.
    """
    counter = Counter()
    for tokens in tokenized_texts:
        counter.update(tokens)
    # Initialize vocabulary with pad and unk tokens
    vocab: Dict[str, int] = {pad_token: 0, unk_token: 1}
    index = 2
    for token, freq in counter.items():
        if freq >= min_freq and token not in vocab:
            vocab[token] = index
            index += 1
    return vocab


def tokens_to_indices(tokens: List[str], vocab: Dict[str, int], unk_token: str = "<unk>") -> List[int]:
    """
    Convert a list of tokens into a list of indices using the provided vocabulary.
    Tokens not found in the vocabulary are replaced with the unk_token index.
    """
    unk_index = vocab.get(unk_token, 1)
    return [vocab.get(token, unk_index) for token in tokens]


def load_text_file(file_path: str) -> List[str]:
    """
    Load a text file and return a list of non-empty stripped lines.
    If the file cannot be read, returns an empty list.
    """
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
        return [line.strip() for line in lines if line.strip()]
    except Exception as error:
        print(f"Warning: Could not load file {file_path}. Error: {error}")
        return []


# ----------------------- Dataset Classes ----------------------- #
class TranslationDataset(Dataset):
    """
    A PyTorch Dataset for machine translation.
    Each example is a dictionary with keys 'src' and 'tgt', which are lists of token indices.
    """
    def __init__(self, data: List[Dict[str, List[int]]]) -> None:
        self.data = data

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, List[int]]:
        return self.data[index]


class ParsingDataset(Dataset):
    """
    A PyTorch Dataset for constituency parsing.
    Each example is a dictionary with key 'input', a list of token indices representing the linearized tree.
    """
    def __init__(self, data: List[Dict[str, List[int]]]) -> None:
        self.data = data

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, List[int]]:
        return self.data[index]


# ----------------------- Collate Functions ----------------------- #
def collate_fn_translation(batch: List[Dict[str, List[int]]]) -> Dict[str, torch.Tensor]:
    """
    Collate function for translation batches.
    Pads source and target sequences to the maximum length within the batch.
    """
    src_list = [torch.tensor(item["src"], dtype=torch.long) for item in batch]
    tgt_list = [torch.tensor(item["tgt"], dtype=torch.long) for item in batch]
    padded_src = pad_sequence(src_list, batch_first=True, padding_value=0)
    padded_tgt = pad_sequence(tgt_list, batch_first=True, padding_value=0)
    return {"src": padded_src, "tgt": padded_tgt}


def collate_fn_parsing(batch: List[Dict[str, List[int]]]) -> Dict[str, torch.Tensor]:
    """
    Collate function for parsing batches.
    Pads input sequences to the maximum length within the batch.
    """
    input_list = [torch.tensor(item["input"], dtype=torch.long) for item in batch]
    padded_input = pad_sequence(input_list, batch_first=True, padding_value=0)
    return {"input": padded_input}


# ----------------------- Custom Batch Sampler ----------------------- #
class TokenCountBatchSampler(Sampler[List[int]]):
    """
    A custom batch sampler that groups examples such that the total number of tokens in the batch
    does not exceed specified thresholds.
    For translation tasks, it considers both source and target token counts.
    For parsing tasks, it considers the single sequence length.
    """
    def __init__(
        self,
        dataset: Dataset,
        max_tokens_source: int,
        max_tokens_target: int,
        task: str = "translation",
        shuffle: bool = False
    ) -> None:
        self.dataset = dataset
        self.max_tokens_source = max_tokens_source
        self.max_tokens_target = max_tokens_target
        self.task = task
        self.shuffle = shuffle

        # Pre-compute lengths for each example for efficient batching.
        self.lengths = []
        for i in range(len(dataset)):
            sample = dataset[i]
            if self.task == "translation":
                src_len = len(sample.get("src", []))
                tgt_len = len(sample.get("tgt", []))
                self.lengths.append((src_len, tgt_len))
            elif self.task == "parsing":
                seq_len = len(sample.get("input", []))
                self.lengths.append(seq_len)
            else:
                raise ValueError(f"Unsupported task: {self.task}")

    def __iter__(self):
        indices = list(range(len(self.dataset)))
        if self.shuffle:
            random.shuffle(indices)
        else:
            # For validation, sort by length to reduce padding waste.
            if self.task == "translation":
                indices.sort(key=lambda i: self.lengths[i][0])
            else:
                indices.sort(key=lambda i: self.lengths[i])

        batch: List[int] = []
        if self.task == "translation":
            tokens_src = 0
            tokens_tgt = 0
            for idx in indices:
                src_len, tgt_len = self.lengths[idx]
                # If adding this sample exceeds token limits, yield current batch.
                if batch and (tokens_src + src_len > self.max_tokens_source or tokens_tgt + tgt_len > self.max_tokens_target):
                    yield batch
                    batch = []
                    tokens_src = 0
                    tokens_tgt = 0
                batch.append(idx)
                tokens_src += src_len
                tokens_tgt += tgt_len
            if batch:
                yield batch
        elif self.task == "parsing":
            tokens = 0
            for idx in indices:
                seq_len = self.lengths[idx]
                if batch and (tokens + seq_len > self.max_tokens_source):
                    yield batch
                    batch = []
                    tokens = 0
                batch.append(idx)
                tokens += seq_len
            if batch:
                yield batch
        else:
            raise ValueError(f"Unsupported task: {self.task}")

    def __len__(self) -> int:
        # This length is approximate; compute by iterating over all batches.
        count = 0
        for _ in self.__iter__():
            count += 1
        return count


# ----------------------- DatasetLoader Class ----------------------- #
class DatasetLoader:
    """
    The DatasetLoader class loads raw data files, applies preprocessing steps
    (tokenization, encoding, and linearization for parsing), and creates batched
    DataLoader objects for training and validation.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        # Set default batching token thresholds
        self.batch_tokens_source: int = config.get("training", {}).get("batch_tokens_source", 25000)
        self.batch_tokens_target: int = config.get("training", {}).get("batch_tokens_target", 25000)
        # Determine task: default to "translation" if not explicitly specified in config.
        self.task: str = config.get("task", "translation")
        if self.task == "translation":
            self.dataset_name: str = config.get("translation", {}).get("dataset", "WMT14_EnDe")
        elif self.task == "parsing":
            self.dataset_name: str = config.get("parsing", {}).get("dataset", "WSJ")
        else:
            raise ValueError(f"Unsupported task: {self.task}")

        # Define special tokens and initialize vocabularies.
        self.pad_token: str = "<pad>"
        self.unk_token: str = "<unk>"
        # For translation
        self.src_vocab: Dict[str, int] = {}
        self.tgt_vocab: Dict[str, int] = {}
        # For parsing
        self.vocab: Dict[str, int] = {}

    def load_data(self) -> Tuple[DataLoader, DataLoader]:
        """
        Load raw training and validation data files, apply preprocessing, and
        return paired DataLoader objects for training and validation.
        """
        if self.task == "translation":
            # Define default file paths for translation data
            train_src_path = os.path.join("data", self.dataset_name, "train.src")
            train_tgt_path = os.path.join("data", self.dataset_name, "train.tgt")
            val_src_path = os.path.join("data", self.dataset_name, "val.src")
            val_tgt_path = os.path.join("data", self.dataset_name, "val.tgt")

            train_src_lines = load_text_file(train_src_path)
            train_tgt_lines = load_text_file(train_tgt_path)
            if not train_src_lines or not train_tgt_lines:
                print("Warning: Translation training files not found. Using dummy training data.")
                train_src_lines = ["I am a student.", "Hello world."]
                train_tgt_lines = ["Je suis un étudiant.", "Bonjour le monde."]

            val_src_lines = load_text_file(val_src_path)
            val_tgt_lines = load_text_file(val_tgt_path)
            if not val_src_lines or not val_tgt_lines:
                print("Warning: Translation validation files not found. Using dummy validation data.")
                val_src_lines = ["I love programming."]
                val_tgt_lines = ["J'aime programmer."]

            # Preprocess raw data
            train_data = self.preprocess_data((train_src_lines, train_tgt_lines), is_training=True)
            val_data = self.preprocess_data((val_src_lines, val_tgt_lines), is_training=False)

            # Create Dataset objects
            translation_train_dataset = TranslationDataset(train_data)
            translation_val_dataset = TranslationDataset(val_data)

            # Create custom batch samplers based on token count
            train_batch_sampler = TokenCountBatchSampler(
                translation_train_dataset,
                self.batch_tokens_source,
                self.batch_tokens_target,
                task="translation",
                shuffle=True
            )
            val_batch_sampler = TokenCountBatchSampler(
                translation_val_dataset,
                self.batch_tokens_source,
                self.batch_tokens_target,
                task="translation",
                shuffle=False
            )

            # Create DataLoader objects with the corresponding collate function
            train_loader = DataLoader(translation_train_dataset, batch_sampler=train_batch_sampler, collate_fn=collate_fn_translation)
            val_loader = DataLoader(translation_val_dataset, batch_sampler=val_batch_sampler, collate_fn=collate_fn_translation)
            return train_loader, val_loader

        elif self.task == "parsing":
            # Define default file paths for parsing data
            train_path = os.path.join("data", self.dataset_name, "train.txt")
            val_path = os.path.join("data", self.dataset_name, "val.txt")

            train_lines = load_text_file(train_path)
            if not train_lines:
                print("Warning: Parsing training file not found. Using dummy training data.")
                train_lines = ["(S (NP I) (VP read (NP a book)))", "(S (NP You) (VP run))"]

            val_lines = load_text_file(val_path)
            if not val_lines:
                print("Warning: Parsing validation file not found. Using dummy validation data.")
                val_lines = ["(S (NP He) (VP sings))"]

            # Preprocess raw data
            train_data = self.preprocess_data(train_lines, is_training=True)
            val_data = self.preprocess_data(val_lines, is_training=False)

            # Create Dataset objects
            parsing_train_dataset = ParsingDataset(train_data)
            parsing_val_dataset = ParsingDataset(val_data)

            # Use batch_tokens_source for both source and target limits in parsing
            train_batch_sampler = TokenCountBatchSampler(
                parsing_train_dataset,
                self.batch_tokens_source,
                self.batch_tokens_source,
                task="parsing",
                shuffle=True
            )
            val_batch_sampler = TokenCountBatchSampler(
                parsing_val_dataset,
                self.batch_tokens_source,
                self.batch_tokens_source,
                task="parsing",
                shuffle=False
            )

            # Create DataLoader objects with the corresponding collate function
            train_loader = DataLoader(parsing_train_dataset, batch_sampler=train_batch_sampler, collate_fn=collate_fn_parsing)
            val_loader = DataLoader(parsing_val_dataset, batch_sampler=val_batch_sampler, collate_fn=collate_fn_parsing)
            return train_loader, val_loader

        else:
            raise ValueError(f"Unsupported task: {self.task}")

    def preprocess_data(self, raw_data: Any, is_training: bool = True) -> List[Dict[str, List[int]]]:
        """
        Preprocess raw data by tokenizing, applying a simulated BPE/word-piece encoding,
        and converting tokens into integer indices using a vocabulary.

        For translation tasks:
            raw_data is expected to be a tuple (list[source sentences], list[target sentences]).
        For parsing tasks:
            raw_data is expected to be a list of raw tree strings.
        """
        if self.task == "translation":
            # Expect raw_data as tuple (source_lines, target_lines)
            src_lines, tgt_lines = raw_data
            tokenized_src = [tokenize(line) for line in src_lines]
            tokenized_tgt = [tokenize(line) for line in tgt_lines]

            if is_training:
                self.src_vocab = build_vocab(tokenized_src, pad_token=self.pad_token, unk_token=self.unk_token)
                self.tgt_vocab = build_vocab(tokenized_tgt, pad_token=self.pad_token, unk_token=self.unk_token)

            src_indices = [tokens_to_indices(tokens, self.src_vocab, unk_token=self.unk_token) for tokens in tokenized_src]
            tgt_indices = [tokens_to_indices(tokens, self.tgt_vocab, unk_token=self.unk_token) for tokens in tokenized_tgt]

            data = [{"src": src, "tgt": tgt} for src, tgt in zip(src_indices, tgt_indices)]
            return data

        elif self.task == "parsing":
            # Expect raw_data as list of strings; linearize the tree structure using standard tokenization.
            tokenized_inputs = [tokenize(line) for line in raw_data]

            if is_training:
                self.vocab = build_vocab(tokenized_inputs, pad_token=self.pad_token, unk_token=self.unk_token)

            input_indices = [tokens_to_indices(tokens, self.vocab, unk_token=self.unk_token) for tokens in tokenized_inputs]

            data = [{"input": inp} for inp in input_indices]
            return data

        else:
            raise ValueError(f"Unsupported task: {self.task}")
