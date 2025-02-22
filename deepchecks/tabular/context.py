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
"""Module for base tabular context."""
import typing as t

import numpy as np
import pandas as pd

from deepchecks.core.context import BaseContext
from deepchecks.core.errors import (DatasetValidationError, DeepchecksNotSupportedError, DeepchecksValueError,
                                    ModelValidationError)
from deepchecks.tabular._shared_docs import docstrings
from deepchecks.tabular.dataset import Dataset
from deepchecks.tabular.metric_utils import DeepcheckScorer, get_default_scorers, init_validate_scorers
from deepchecks.tabular.metric_utils.scorers import validate_proba
from deepchecks.tabular.utils.feature_importance import (calculate_feature_importance_or_none,
                                                         validate_feature_importance)
from deepchecks.tabular.utils.task_inference import (get_all_labels, infer_classes_from_model,
                                                     infer_task_type_by_class_number, infer_task_type_by_labels)
from deepchecks.tabular.utils.task_type import TaskType
from deepchecks.tabular.utils.validation import (ensure_predictions_proba, ensure_predictions_shape,
                                                 model_type_validation, validate_model)
from deepchecks.utils.docref import doclink
from deepchecks.utils.logger import get_logger
from deepchecks.utils.plot import DEFAULT_DATASET_NAMES
from deepchecks.utils.typing import BasicModel

__all__ = [
    'Context', '_DummyModel'
]


class _DummyModel:
    """Dummy model class used for inference with static predictions from the user.

    Parameters
    ----------
    train: Dataset
        Dataset, representing data an estimator was fitted on.
    test: Dataset
        Dataset, representing data an estimator predicts on.
    y_pred_train: t.Optional[np.ndarray]
        Array of the model prediction over the train dataset.
    y_pred_test: t.Optional[np.ndarray]
        Array of the model prediction over the test dataset.
    y_proba_train: np.ndarray
        Array of the model prediction probabilities over the train dataset.
    y_proba_test: np.ndarray
        Array of the model prediction probabilities over the test dataset.
    validate_data_on_predict: bool, default = True
        If true, before predicting validates that the received data samples have the same index as in original data.
    """

    feature_df_list: t.List[pd.DataFrame]
    predictions: pd.DataFrame
    proba: pd.DataFrame

    def __init__(self,
                 test: Dataset,
                 y_proba_test: t.Optional[np.ndarray] = None,
                 y_pred_test: t.Optional[np.ndarray] = None,
                 train: t.Union[Dataset, None] = None,
                 y_pred_train: t.Optional[np.ndarray] = None,
                 y_proba_train: t.Optional[np.ndarray] = None,
                 validate_data_on_predict: bool = True,
                 model_classes: t.Optional[t.List] = None):

        if train is not None and test is not None:
            # check if datasets have same indexes
            if set(train.data.index) & set(test.data.index):
                train.data.index = map(lambda x: f'train-{x}', list(train.data.index))
                test.data.index = map(lambda x: f'test-{x}', list(test.data.index))
                get_logger().warning('train and test datasets have common index - adding "train"/"test"'
                                     ' prefixes. To avoid that provide datasets with no common indexes '
                                     'or pass the model object instead of the predictions.')

        feature_df_list = []
        predictions = []
        probas = []

        for dataset, y_pred, y_proba in zip([train, test],
                                            [y_pred_train, y_pred_test],
                                            [y_proba_train, y_proba_test]):
            if y_pred is not None and not isinstance(y_pred, np.ndarray):
                y_pred = np.array(y_pred)
            if y_proba is not None and not isinstance(y_proba, np.ndarray):
                y_proba = np.array(y_proba)
            if dataset is not None:
                feature_df_list.append(dataset.features_columns)
                if y_pred is None and y_proba is not None:
                    validate_proba(y_proba, model_classes)
                    y_pred = np.argmax(y_proba, axis=-1)
                    y_pred = np.array(model_classes)[y_pred]
                if y_pred is not None:
                    if len(y_pred.shape) > 1 and y_pred.shape[1] == 1:
                        y_pred = y_pred[:, 0]
                    ensure_predictions_shape(y_pred, dataset.data)
                    y_pred_ser = pd.Series(y_pred, index=dataset.data.index)
                    predictions.append(y_pred_ser)
                    if y_proba is not None:
                        ensure_predictions_proba(y_proba, y_pred)
                        proba_df = pd.DataFrame(data=y_proba)
                        proba_df.index = dataset.data.index
                        probas.append(proba_df)

        self.predictions = pd.concat(predictions, axis=0) if predictions else None
        self.probas = pd.concat(probas, axis=0) if probas else None
        self.feature_df_list = feature_df_list
        self.validate_data_on_predict = validate_data_on_predict

        if self.predictions is not None:
            self.predict = self._predict

        if self.probas is not None:
            self.predict_proba = self._predict_proba

    def _validate_data(self, data: pd.DataFrame):
        data = data.sample(min(100, len(data)))
        for feature_df in self.feature_df_list:
            # If all indices are found than test for equality in actual data (statistically significant portion)
            if set(data.index).issubset(set(feature_df.index)):
                sample_data = np.unique(np.random.choice(data.index, 30))
                if feature_df.loc[sample_data].equals(data.loc[sample_data]):
                    return
                else:
                    break
        raise DeepchecksValueError('Data that has not been seen before passed for inference with static '
                                   'predictions. Pass a real model to resolve this')

    def _predict(self, data: pd.DataFrame):
        """Predict on given data by the data indexes."""
        if self.validate_data_on_predict:
            self._validate_data(data)
        return self.predictions.loc[data.index].to_numpy()

    def _predict_proba(self, data: pd.DataFrame):
        """Predict probabilities on given data by the data indexes."""
        if self.validate_data_on_predict:
            self._validate_data(data)
        return self.probas.loc[data.index].to_numpy()

    def fit(self, *args, **kwargs):
        """Just for python 3.6 (sklearn validates fit method)."""


@docstrings
class Context(BaseContext):
    """Contains all the data + properties the user has passed to a check/suite, and validates it seamlessly.

    Parameters
    ----------
    train: Union[Dataset, pd.DataFrame, None] , default: None
        Dataset or DataFrame object, representing data an estimator was fitted on
    test: Union[Dataset, pd.DataFrame, None] , default: None
        Dataset or DataFrame object, representing data an estimator predicts on
    model: Optional[BasicModel] , default: None
        A scikit-learn-compatible fitted estimator instance
    {additional_context_params:indent}
    """

    def __init__(
            self,
            train: t.Union[Dataset, pd.DataFrame, None] = None,
            test: t.Union[Dataset, pd.DataFrame, None] = None,
            model: t.Optional[BasicModel] = None,
            feature_importance: t.Optional[pd.Series] = None,
            feature_importance_force_permutation: bool = False,
            feature_importance_timeout: int = 120,
            with_display: bool = True,
            y_pred_train: t.Optional[np.ndarray] = None,
            y_pred_test: t.Optional[np.ndarray] = None,
            y_proba_train: t.Optional[np.ndarray] = None,
            y_proba_test: t.Optional[np.ndarray] = None,
            model_classes: t.Optional[t.List] = None,
    ):
        # Validations
        if train is None and test is None and model is None:
            raise DeepchecksValueError('At least one dataset (or model) must be passed to the method!')
        if train is not None:
            train = Dataset.cast_to_dataset(train)
            if train.name is None:
                train.name = DEFAULT_DATASET_NAMES[0]
        if test is not None:
            test = Dataset.cast_to_dataset(test)
            if test.name is None:
                test.name = DEFAULT_DATASET_NAMES[1]
        # If both dataset, validate they fit each other
        if train and test:
            if test.has_label() and train.has_label() and not Dataset.datasets_share_label(train, test):
                raise DatasetValidationError('train and test requires to have and to share the same label')
            if not Dataset.datasets_share_features(train, test):
                raise DatasetValidationError('train and test requires to share the same features columns')
            if not Dataset.datasets_share_categorical_features(train, test):
                raise DatasetValidationError(
                    'train and test datasets should share '
                    'the same categorical features. Possible reason is that some columns were'
                    'inferred incorrectly as categorical features. To fix this, manually edit the '
                    'categorical features using Dataset(cat_features=<list_of_features>'
                )
            if not Dataset.datasets_share_index(train, test):
                raise DatasetValidationError('train and test requires to share the same index column')
            if not Dataset.datasets_share_date(train, test):
                raise DatasetValidationError('train and test requires to share the same date column')
        if test and not train:
            raise DatasetValidationError('Can\'t initialize context with only test. if you have single dataset, '
                                         'initialize it as train')
        self._calculated_importance = feature_importance is not None or model is None
        if model is not None:
            # Here validate only type of model, later validating it can predict on the data if needed
            model_type_validation(model)
        if feature_importance is not None:
            feature_importance = validate_feature_importance(feature_importance, train.features)
        if model_classes and len(model_classes) == 0:
            raise DeepchecksValueError('Received empty model_classes')
        if model_classes and sorted(model_classes) != model_classes:
            supported_models_link = doclink(
                'supported-prediction-format',
                template='For more information please refer to the Supported Models guide {link}')
            raise DeepchecksValueError(f'Received unsorted model_classes. {supported_models_link}')

        if model_classes is None:
            model_classes = infer_classes_from_model(model)
        labels = None
        if train and train.label_type:
            task_type = train.label_type
        elif model_classes:
            task_type = infer_task_type_by_class_number(len(model_classes))
        else:
            labels = get_all_labels(model, train, test, y_pred_train, y_pred_test)
            task_type = infer_task_type_by_labels(labels)

        observed_classes = None

        if (model is None and
                (y_pred_train is not None or y_pred_test is not None or y_proba_train is not None
                 or y_proba_test is not None)):
            # If there is no pred, we use the observed classes to zip between the proba and the classes
            if y_pred_train is None and model_classes is None:
                # Does not calculate labels twice
                labels = labels if labels is not None else get_all_labels(model, train, test, y_pred_train, y_pred_test)
                observed_classes = sorted(labels.dropna().unique().tolist())
            model = _DummyModel(train=train, test=test,
                                y_pred_train=y_pred_train, y_pred_test=y_pred_test,
                                y_proba_test=y_proba_test, y_proba_train=y_proba_train,
                                # Use model classes if exists, else observed classes
                                model_classes=model_classes or observed_classes)

        self._task_type = task_type
        self._observed_classes = observed_classes
        self._model_classes = model_classes
        self._train = train
        self._test = test
        self._model = model
        self._feature_importance_force_permutation = feature_importance_force_permutation
        self._feature_importance = feature_importance
        self._feature_importance_timeout = feature_importance_timeout
        self._importance_type = None
        self._validated_model = False
        self._with_display = with_display

    # Properties
    # Validations note: We know train & test fit each other so all validations can be run only on train

    @property
    def model(self) -> BasicModel:
        """Return & validate model if model exists, otherwise raise error."""
        if self._model is None:
            raise DeepchecksNotSupportedError('Check is irrelevant for Datasets without model')
        if not self._validated_model:
            if self._train:
                validate_model(self._train, self._model)
            self._validated_model = True
        return self._model

    @property
    def model_classes(self) -> t.List:
        """Return ordered list of possible label classes for classification tasks or None for regression."""
        if self._model_classes is None and self.task_type in (TaskType.BINARY, TaskType.MULTICLASS):
            # If in infer_task_type we didn't find classes on model, or user didn't pass any, then using the observed
            get_logger().warning('Could not find model\'s classes, using the observed classes')
            return self.observed_classes
        return self._model_classes

    @property
    def observed_classes(self) -> t.List:
        """Return the observed classes in both train and test. None for regression."""
        # If did not cache yet the observed classes than calculate them
        if self._observed_classes is None and self.task_type in (TaskType.BINARY, TaskType.MULTICLASS):
            labels = get_all_labels(self._model, self._train, self._test)
            self._observed_classes = sorted(labels.dropna().unique().tolist())
        return self._observed_classes

    @property
    def model_name(self):
        """Return model name."""
        return type(self.model).__name__

    @property
    def task_type(self) -> TaskType:
        """Return task type based on calculated classes argument."""
        return self._task_type

    @property
    def feature_importance(self) -> t.Optional[pd.Series]:
        """Return feature importance, or None if not possible."""
        if not self._calculated_importance:
            if self._model and (self._train or self._test):
                permutation_kwargs = {'timeout': self._feature_importance_timeout}
                dataset = self.test if self.have_test() else self.train
                importance, importance_type = calculate_feature_importance_or_none(
                    self._model, dataset, self.model_classes, self._observed_classes, self.task_type,
                    self._feature_importance_force_permutation, permutation_kwargs
                )
                self._feature_importance = importance
                self._importance_type = importance_type
            else:
                self._feature_importance = None
            self._calculated_importance = True

        return self._feature_importance

    @property
    def feature_importance_timeout(self) -> t.Optional[int]:
        """Return feature importance timeout."""
        return self._feature_importance_timeout

    @property
    def feature_importance_type(self) -> t.Optional[str]:
        """Return feature importance type if feature importance is available, else None."""
        # Calling first feature_importance, because _importance_type is assigned only after feature importance is
        # calculated.
        if self.feature_importance:
            return self._importance_type
        return None

    def have_test(self):
        """Return whether there is test dataset defined."""
        return self._test is not None

    def assert_classification_task(self):
        """Assert the task_type is classification."""
        if self.task_type == TaskType.REGRESSION and self.train.has_label():
            raise ModelValidationError('Check is irrelevant for regression tasks')

    def assert_regression_task(self):
        """Assert the task type is regression."""
        if self.task_type != TaskType.REGRESSION and self.train.has_label():
            raise ModelValidationError('Check is irrelevant for classification tasks')

    def get_scorers(self,
                    scorers: t.Union[t.Mapping[str, t.Union[str, t.Callable]], t.List[str]] = None,
                    use_avg_defaults=True) -> t.List[DeepcheckScorer]:
        """Return initialized & validated scorers if provided or default scorers otherwise.

        Parameters
        ----------
        scorers : Union[List[str], Dict[str, Union[str, Callable]]], default: None
            List of scorers to use. If None, use default scorers.
            Scorers can be supplied as a list of scorer names or as a dictionary of names and functions.
        use_avg_defaults : bool, default True
            If no scorers were provided, for classification, determines whether to use default scorers that return
            an averaged metric, or default scorers that return a metric per class.
        Returns
        -------
        List[DeepcheckScorer]
            A list of initialized & validated scorers.
        """
        scorers = scorers or get_default_scorers(self.task_type, use_avg_defaults)
        return init_validate_scorers(scorers, self.model, self.train, self.model_classes, self.observed_classes)

    def get_single_scorer(self,
                          scorer: t.Mapping[str, t.Union[str, t.Callable]] = None,
                          use_avg_defaults=True) -> DeepcheckScorer:
        """Return initialized & validated scorer if provided or a default scorer otherwise.

        Parameters
        ----------
        scorer : Union[List[str], Dict[str, Union[str, Callable]]], default: None
            List of scorers to use. If None, use default scorers.
            Scorers can be supplied as a list of scorer names or as a dictionary of names and functions.
        use_avg_defaults : bool, default True
            If no scorers were provided, for classification, determines whether to use default scorers that return
            an averaged metric, or default scorers that return a metric per class.
        Returns
        -------
        List[DeepcheckScorer]
            An initialized & validated scorer.
        """
        scorer = scorer or get_default_scorers(self.task_type, use_avg_defaults)
        # The single scorer is the first one in the dict
        scorer_name = next(iter(scorer))
        single_scorer_dict = {scorer_name: scorer[scorer_name]}
        return init_validate_scorers(single_scorer_dict, self.model, self.train, self.model_classes,
                                     self.observed_classes)[0]
