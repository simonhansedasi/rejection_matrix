# Project Sketch — PySpark GrapeExpectations Pipeline

**Goal:** Reimplement the GrapeExpectations ML pipeline in PySpark to gain citable distributed compute experience. The dataset is too small to need Spark; that is fine. The skill is the point.

**Time estimate:** 3-5 hours

---

## Setup

```bash
pip install pyspark
```

No cluster needed. PySpark runs in local mode with `SparkSession.builder.master("local[*]")`. All cores, no HDFS.

Working directory: `GeoGastronomy/GrapeExpectations/RegressionRidge/ML/`

---

## Step 1 — Export the training data to Parquet

The existing pipeline stores data in `.pkl` files. PySpark reads Parquet natively; convert once and keep it.

```python
import pandas as pd

df = pd.read_pickle("../data_wrangling/df_smoothed.pkl")
# or df_clustered.pkl if it already has the full feature matrix + target
df.to_parquet("grape_training.parquet", index=False)
```

Verify columns: confirm which column is the NDVI target, which are features, and whether `year` is present for LOYO splits.

---

## Step 2 — Load into Spark

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .master("local[*]") \
    .appName("GrapeExpectations") \
    .getOrCreate()

df = spark.read.parquet("grape_training.parquet")
df.printSchema()
df.describe().show()
```

---

## Step 3 — Feature assembly and scaling

PySpark MLlib expects a single `features` vector column. Use `VectorAssembler` to pack all feature columns, then `StandardScaler`.

```python
from pyspark.ml.feature import VectorAssembler, StandardScaler

feature_cols = [c for c in df.columns if c not in ("ndvi", "year", "hex_id")]

assembler = VectorAssembler(inputCols=feature_cols, outputCol="raw_features")
scaler = StandardScaler(inputCol="raw_features", outputCol="features",
                        withMean=True, withStd=True)
```

---

## Step 4 — Train a GBT regressor

Gradient boosted trees in MLlib, predicting NDVI.

```python
from pyspark.ml.regression import GBTRegressor
from pyspark.ml import Pipeline
from pyspark.ml.evaluation import RegressionEvaluator

gbt = GBTRegressor(featuresCol="features", labelCol="ndvi",
                   maxIter=100, maxDepth=5, seed=42)

pipeline = Pipeline(stages=[assembler, scaler, gbt])

train, test = df.filter(df.year != 2022), df.filter(df.year == 2022)
model = pipeline.fit(train)
preds = model.transform(test)

evaluator = RegressionEvaluator(labelCol="ndvi", predictionCol="prediction")
print("R²:", evaluator.evaluate(preds, {evaluator.metricName: "r2"}))
print("RMSE:", evaluator.evaluate(preds, {evaluator.metricName: "rmse"}))
```

---

## Step 5 — LOYO validation

Mirror the existing LOYO notebooks but in Spark. Loop over years, train on all-but-one, evaluate on the held-out year.

```python
years = [row.year for row in df.select("year").distinct().collect()]
r2_scores = []

for held_out in sorted(years):
    train = df.filter(df.year != held_out)
    test  = df.filter(df.year == held_out)
    m = pipeline.fit(train)
    p = m.transform(test)
    r2_scores.append(evaluator.evaluate(p, {evaluator.metricName: "r2"}))

import numpy as np
print(f"LOYO mean R²: {np.mean(r2_scores):.3f} ± {np.std(r2_scores):.3f}")
```

Target: reproduce the existing mean R² = 0.353 ± 0.114. If it's close, the pipeline is correct.

---

## Step 6 — Feature importance

GBT models in MLlib expose feature importances directly.

```python
gbt_model = model.stages[-1]
importances = gbt_model.featureImportances.toArray()
pairs = sorted(zip(feature_cols, importances), key=lambda x: -x[1])
for name, score in pairs[:10]:
    print(f"{name}: {score:.4f}")
```

Confirm terrain features still dominate (~75% of importance, matching the SHAP result from the existing analysis).

---

## What to add to the resume

```
Reimplemented a 32,382-row, 100+ feature ensemble regression pipeline in PySpark MLlib
(VectorAssembler, StandardScaler, GBTRegressor); reproduced LOYO temporal validation
(mean R² = 0.353 ± 0.114 across 9 vintages) using Spark's distributed execution in local mode.
```

Or shorter: "Ported GrapeExpectations ML pipeline to PySpark; reproduced LOYO R² within [X] of sklearn baseline."

---

## Files to create

- `GrapeExpectations/RegressionRidge/ML/spark_pipeline.py` — clean script version
- `GrapeExpectations/RegressionRidge/ML/grape_training.parquet` — exported training data
- Optionally: a short notebook `spark_loyo.ipynb` alongside the existing `loyo_validation.ipynb`
