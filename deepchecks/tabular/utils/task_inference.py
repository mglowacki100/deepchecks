# ----------------------------------------------------------------------------
# Copyright (C) 2021-2022 Deepchecks (https://www.deepchecks.com)
#
# This file is part of Deepchecks.
# Deepchecks is distributed under the terms of the GNU Affero General
# Public License (version 3 or later).
# You should have received a copy of the GNU Affero General Public License
# along with Deepchecks.  If not, see <http://www.gnu.org/licenses/>.
# ----------------------------------------------------------------------------
#
"""Utils module containing functionalities to infer the task type and possible label classes."""

__all__ = ['get_possible_classes', 'infer_task_type']

from typing import List, Optional

import pandas as pd
from sklearn.base import ClassifierMixin

from deepchecks import tabular
from deepchecks.core.errors import DeepchecksValueError
from deepchecks.tabular.utils.feature_inference import is_categorical
from deepchecks.tabular.utils.task_type import TaskType
from deepchecks.utils.docref import doclink
from deepchecks.utils.strings import is_string_column
from deepchecks.utils.typing import BasicModel


# pylint: disable=protected-access
def get_possible_classes(model: Optional[BasicModel], train_dataset: 'tabular.Dataset',
                         test_dataset: Optional['tabular.Dataset'] = None, force_classification: bool = False) \
        -> Optional[List]:
    """Return the list of allowed classes for classification tasks or None for regression.

    Parameters
    ----------
    model : BasicModel
        Model object used in task
    train_dataset : 'tabular.Dataset'
        Train Dataset of task
    test_dataset : Optional['tabular.Dataset'], default = None
        Test Dataset of task
    force_classification: bool, default = False
        Whether to disable the auto infer for task type and return all observed label values.
    Returns
    -------
    Optional[List]
        The list of possible classes for classification tasks or None for regression
    """
    if not isinstance(train_dataset, tabular.Dataset) or (test_dataset is not None and
                                                          not isinstance(test_dataset, tabular.Dataset)):
        raise DeepchecksValueError('train_dataset and test_dataset must be of type tabular.Dataset')

    if train_dataset._label_classes is not None:
        if hasattr(model, 'classes_') and len(model.classes_) > 0 and \
                list(model.classes_) != train_dataset._label_classes:
            raise DeepchecksValueError('Model output classes and train dataset label classes do not match')
        return train_dataset._label_classes

    if hasattr(model, 'classes_') and len(model.classes_) > 0:
        return list(model.classes_)

    if model is not None and not hasattr(model, 'predict_proba') and not force_classification:
        if is_string_column(train_dataset.label_col):
            reference = doclink('supported-prediction-format', template='For additional details see {link}')
            raise DeepchecksValueError(f'Classification models must contain \'predict_proba\' functionality. '
                                       f'{reference}')
        if isinstance(model, ClassifierMixin):
            raise DeepchecksValueError('Model is a sklearn classification model but lacks the predict_proba method. '
                                       'Please train the model with probability=True.')
        return None

    observed_labels = list(train_dataset.label_col)
    if test_dataset is not None:
        observed_labels += list(test_dataset.label_col)

    if model is not None:
        observed_labels += list(model.predict(train_dataset.features_columns))
        if test_dataset is not None:
            observed_labels += list(model.predict(test_dataset.features_columns))
        return sorted(pd.Series(observed_labels).dropna().unique())
    elif is_categorical(pd.Series(observed_labels), max_categorical_ratio=0.05) or force_classification:
        return sorted(pd.Series(observed_labels).dropna().unique())
    else:  # if regression
        return None


def infer_task_type(model: Optional[BasicModel], train_dataset: 'tabular.Dataset',
                    test_dataset: Optional['tabular.Dataset'] = None) -> TaskType:
    """Infer the task type based on get_possible_classes.

    Parameters
    ----------
    model : BasicModel
        Model object used in task
    train_dataset : 'tabular.Dataset'
        Train Dataset of task
    test_dataset : Optional['tabular.Dataset'], default = None
        Test Dataset of task
    Returns
    -------
    TaskType
        The type of the Task
    """
    classes = get_possible_classes(model, train_dataset, test_dataset)
    if classes is None:
        return TaskType.REGRESSION
    elif len(classes) == 2:
        return TaskType.BINARY
    else:
        return TaskType.MULTICLASS
