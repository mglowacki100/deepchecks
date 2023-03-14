# ----------------------------------------------------------------------------
# Copyright (C) 2021-2023 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
#
"""Module of TextPropertyOutliers check."""
import string
import typing as t
from collections import defaultdict
from numbers import Number
from secrets import choice

import numpy as np
import pandas as pd

from deepchecks.core import CheckResult, DatasetKind
from deepchecks.core.errors import DeepchecksProcessError, NotEnoughSamplesError
from deepchecks.nlp import Context, SingleDatasetCheck, TextData
from deepchecks.nlp.utils.text_utils import trim
from deepchecks.utils.distribution.plot import feature_distribution_traces, get_property_outlier_graph
from deepchecks.utils.outliers import iqr_outliers_range
from deepchecks.utils.strings import format_number

__all__ = ['TextPropertyOutliers']

THUMBNAIL_SIZE = (200, 200)


class TextPropertyOutliers(SingleDatasetCheck):
    """Find outliers samples with respect to the given properties.

    The check computes several properties and then computes the number of outliers for each property.
    The check uses `IQR <https://en.wikipedia.org/wiki/Interquartile_range#Outliers>`_ to detect outliers out of the
    single dimension properties.

    Parameters
    ----------
    properties : List[Dict[str, Any]], default: None
        List of properties. Replaces the default deepchecks properties.
        Each property is a dictionary with keys ``'name'`` (str), ``method`` (Callable) and ``'output_type'`` (str),
        representing attributes of said method. 'output_type' must be one of:

        - ``'numeric'`` - for continuous ordinal outputs.
        - ``'categorical'`` - for discrete, non-ordinal outputs. These can still be numbers,
          but these numbers do not have inherent value.
        - ``'class_id'`` - for properties that return the class_id. This is used because these
          properties are later matched with the ``VisionData.label_map``, if one was given.

        For more on image / label properties, see the guide about :ref:`vision_properties_guide`.
    n_show_top : int , default: 5
        number of outliers to show from each direction (upper limit and bottom limit)
    iqr_percentiles: Tuple[int, int], default: (25, 75)
        Two percentiles which define the IQR range
    iqr_scale: float, default: 1.5
        The scale to multiply the IQR range for the outliers detection
    """
    """Find outliers images with respect to the given properties.

    The check computes several image properties and then computes the number of outliers for each property.
    The check uses `IQR <https://en.wikipedia.org/wiki/Interquartile_range#Outliers>`_ to detect outliers out of the
    single dimension properties.

    Parameters
    ----------
    text_properties : List[Dict[str, Any]], default: None
        List of properties. Replaces the default deepchecks properties.
        Each property is a dictionary with keys ``'name'`` (str), ``method`` (Callable) and ``'output_type'`` (str),
        representing attributes of said method. 'output_type' must be one of:

        - ``'numeric'`` - for continuous ordinal outputs.
        - ``'categorical'`` - for discrete, non-ordinal outputs. These can still be numbers,
          but these numbers do not have inherent value.

        For more on image / label properties, see the guide about :ref:`vision_properties_guide`.
    n_show_top : int , default: 5
        number of outliers to show from each direction (upper limit and bottom limit)
    iqr_percentiles: Tuple[int, int], default: (25, 75)
        Two percentiles which define the IQR range
    iqr_scale: float, default: 1.5
        The scale to multiply the IQR range for the outliers detection
    """

    def __init__(self,
                 properties_list: t.List[t.Dict[str, t.Any]] = None,
                 n_show_top: int = 5,
                 iqr_percentiles: t.Tuple[int, int] = (25, 75),
                 iqr_scale: float = 1.5,
                 **kwargs):
        super().__init__(**kwargs)
        self.properties_list = properties_list
        self.iqr_percentiles = iqr_percentiles
        self.iqr_scale = iqr_scale
        self.n_show_top = n_show_top

    def run_logic(self, context: Context, dataset_kind: DatasetKind) -> CheckResult:
        """Compute final result."""
        dataset = context.get_data_by_kind(dataset_kind)
        corpus = defaultdict(list)
        result = {}

        property_types = dataset.properties_types
        df_properties = dataset.properties[[col for col in dataset.properties.columns if property_types[col] == 'numeric']]
        properties = df_properties.to_dict(orient='list')

        # The values are in the same order as the batch order, so always keeps the same order in order to access
        # the original sample at this index location
        for name, values in properties.items():
            # If the property is single value per sample, then wrap the values in list in order to work on fixed
            # structure
            if not isinstance(values[0], list):
                values = [[x] for x in values]

            values_lengths_cumsum = np.cumsum(np.array([len(v) for v in values]))
            values_arr = np.hstack(values).astype(float)

            try:
                lower_limit, upper_limit = iqr_outliers_range(values_arr, self.iqr_percentiles, self.iqr_scale)
            except NotEnoughSamplesError:
                result[name] = 'Not enough non-null samples to calculate outliers.'
                continue

            # Get the indices of the outliers
            top_outliers = np.argwhere(values_arr > upper_limit).squeeze(axis=1)
            # Sort the indices of the outliers by the original values
            top_outliers = top_outliers[
                np.apply_along_axis(lambda i, sort_arr=values_arr: sort_arr[i], axis=0, arr=top_outliers).argsort()
            ]

            # Doing the same for bottom outliers
            bottom_outliers = np.argwhere(values_arr < lower_limit).squeeze(axis=1)
            # Sort the indices of the outliers by the original values
            bottom_outliers = bottom_outliers[
                np.apply_along_axis(lambda i, sort_arr=values_arr: sort_arr[i], axis=0, arr=bottom_outliers).argsort()
            ]

            # Take the indices to show images from the top and bottom
            show_indices = np.concatenate((bottom_outliers[:self.n_show_top], top_outliers[-self.n_show_top:]))

            # Calculate cumulative sum of the outliers lengths in order to find the correct index of the image
            for outlier_index in show_indices:
                sample_index = _sample_index_from_flatten_index(values_lengths_cumsum, outlier_index)
                value = values_arr[outlier_index].item()
                # To get the value index inside the properties list of a single sample we take the sum of values
                # and decrease the current outlier index. Then we get the value index from the end of the sample list.
                index_of_value_in_sample = (values_lengths_cumsum[sample_index] - outlier_index) * -1
                num_properties_in_sample = len(values[sample_index])

                text = self.plot_text(dataset, sample_index, index_of_value_in_sample, num_properties_in_sample)
                corpus[name].append((value, text))

            # Calculate for all outliers the image index
            text_outliers = [_sample_index_from_flatten_index(values_lengths_cumsum, outlier_index) for
                              outlier_index in np.concatenate((bottom_outliers, top_outliers))]

            result[name] = {
                'indices': [dataset.index[i] for i in text_outliers],
                # For the upper and lower limits doesn't show values that are smaller/larger than the actual values
                # we have in the data
                'lower_limit': max(lower_limit, min(values_arr)),
                'upper_limit': min(upper_limit, max(values_arr)),
            }

        # Create display
        if context.with_display:
            display = []
            no_outliers = pd.Series([], dtype='object')

            for property_name, info in result.items():
                # If info is string it means there was error
                if isinstance(info, str):
                    no_outliers = pd.concat([no_outliers, pd.Series(property_name, index=[info])])
                elif len(info['indices']) == 0:
                    no_outliers = pd.concat([no_outliers, pd.Series(property_name, index=['No outliers found.'])])
                else:
                    dist = df_properties[property_name]
                    lower_limit = info['lower_limit']
                    upper_limit = info['upper_limit']

                    fig = get_property_outlier_graph(dist, dataset.text, lower_limit, upper_limit, property_name)


                    display.append(fig)

            if not no_outliers.empty:
                grouped = no_outliers.groupby(level=0).unique().str.join(', ')
                grouped_df = pd.DataFrame(grouped, columns=['Properties'])
                grouped_df['More Info'] = grouped_df.index
                grouped_df = grouped_df[['More Info', 'Properties']]
                display.append('<h5><b>Properties With No Outliers Found</h5></b>')
                display.append(grouped_df.style.hide(axis='index') if hasattr(grouped_df.style, 'hide') else
                               grouped_df.style.hide_index())

        else:
            display = None

        return CheckResult(result, display=display)

    def plot_text(self, data: TextData, sample_index: int, index_of_value_in_sample: int,
                  num_properties_in_sample: int) -> np.ndarray:
        """Return an image to show as output of the display.

        Parameters
        ----------
        data : TextData
            The text data object used in the check.
        sample_index : int
            The batch index of the sample to draw the image for.
        index_of_value_in_sample : int
            Each sample property is list, then this is the index of the outlier in the sample property list.
        num_properties_in_sample
            The number of values in the sample's property list.
        """
        return trim(data.text[sample_index], 100)

    def get_default_properties(self, data: TextData):
        """Return default properties to run in the check."""
        return data.properties


def _ensure_property_shape(property_values, data_len, prop_name):
    """Validate the result of the property."""
    if len(property_values) != data_len:
        raise DeepchecksProcessError(f'Properties are expected to return value per image but instead got'
                                     f' {len(property_values)} values for {data_len} images for property '
                                     f'{prop_name}')

    # If the first item is list validate all items are list of numbers
    if isinstance(property_values[0], t.Sequence):
        if any((not isinstance(x, t.Sequence) for x in property_values)):
            raise DeepchecksProcessError(f'Property result is expected to be either all lists or all scalars but'
                                         f' got mix for property {prop_name}')
        if any((not _is_list_of_numbers(x) for x in property_values)):
            raise DeepchecksProcessError(f'For outliers, properties are expected to be only numeric types but'
                                         f' found non-numeric value for property {prop_name}')
    # If first value is not list, validate all items are numeric
    elif not _is_list_of_numbers(property_values):
        raise DeepchecksProcessError(f'For outliers, properties are expected to be only numeric types but'
                                     f' found non-numeric value for property {prop_name}')


def _is_list_of_numbers(l):
    return not any(i is not None and not isinstance(i, Number) for i in l)


def _sample_index_from_flatten_index(cumsum_lengths, flatten_index) -> int:
    # The cumulative sum lengths is holding the cumulative sum of properties per image, so the first index which value
    # is greater than the flatten index, is the image index.
    # for example if the sums lengths is [1, 6, 11, 13, 16, 20] and the flatten index = 6, it means this property
    # belong to the third image which is index = 2.
    return np.argwhere(cumsum_lengths > flatten_index)[0][0]


NO_IMAGES_TEMPLATE = """
<h3><b>Property "{prop_name}"</b></h3>
<div>{message}</div>
"""

HTML_TEMPLATE = """
<style>
    .{id}-container {{
        overflow-x: auto;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }}
    .{id}-row {{
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 10px;
    }}
    .{id}-item {{
      display: flex;
      min-width: 200px;
      position: relative;
      word-wrap: break-word;
      align-items: center;
      justify-content: center;
    }}
    .{id}-title {{
        font-family: "Open Sans", verdana, arial, sans-serif;
        color: #2a3f5f
    }}
    /* A fix for jupyter widget which doesn't have width defined on HTML widget */
    .widget-html-content {{
        width: -moz-available;          /* WebKit-based browsers will ignore this. */
        width: -webkit-fill-available;  /* Mozilla-based browsers will ignore this. */
        width: fill-available;
    }}
</style>
<h5><b>Property "{prop_name}"</b></h5>
<div>
Total number of outliers: {count}
</div>
<div>
Non-outliers range: {lower_limit} to {upper_limit}
</div>
<div class="{id}-container">
    <div class="{id}-row">
        <h5 class="{id}-item">{prop_name}</h5>
        {values}
    </div>
    <div class="{id}-row">
        <h5 class="{id}-item">Text</h5>
        {text}
    </div>
</div>
"""

