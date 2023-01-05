from typing import Optional, List

from .table import Table


class HuggingFaceTable(Table):
    DEFAULT_FOLDER_PATH = '/runhouse/huggingface-tables'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    def from_config(config: dict, **kwargs):
        """ Load config values into the object. """
        return HuggingFaceTable(**config)

    def save(self,
             name: Optional[str] = None,
             snapshot: bool = False,
             save_to: Optional[List[str]] = None,
             overwrite: bool = False,
             **snapshot_kwargs):
        if self._cached_data is None or overwrite:
            import datasets
            if isinstance(self.data, datasets.arrow_dataset.Dataset):
                # Under the hood we convert to a pyarrow table before saving to the file system
                pa_table = self.data.data.table
                self.data = pa_table
            elif isinstance(self.data, datasets.arrow_dataset.DatasetDict):
                # TODO [JL] Add support for dataset dict
                raise NotImplementedError('Runhouse does not currently support DatasetDict objects, please convert to '
                                          'a Dataset before saving.')

            super().save(name=name,
                         save_to=save_to if save_to is not None else self.save_to,
                         snapshot=snapshot,
                         overwrite=overwrite,
                         **snapshot_kwargs)

    def fetch(self, **kwargs):
        # TODO [JL] Add support for dataset dict
        from datasets import Dataset
        # Read as pyarrow table, then convert back to HF dataset
        pa_table = super().fetch(**kwargs)
        self._cached_data = Dataset(pa_table)
        return self._cached_data

    def stream(self, batch_size, drop_last: bool = False, shuffle_seed: Optional[int] = None):
        from datasets import Dataset
        batches = super().stream(batch_size, drop_last, shuffle_seed)
        # convert to HF dataset before returning
        hf_batches = [Dataset.from_pandas(batch.to_pandas()) for batch in batches]
        return hf_batches
