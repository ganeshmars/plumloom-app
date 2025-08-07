# app/utils/csv_data_processor.py
import pandas as pd
import numpy as np
import io
from datetime import datetime
from app.core.logging_config import logger


def validate_csv(file_content_bytes: bytes, filename: str):
    """
    Validate the uploaded CSV file

    Args:
        file_content_bytes: The byte content of the file
        filename: The name of the file

    Returns:
        dict: A dictionary with validation result and either data or error message
    """
    try:
        if not filename.endswith('.csv'):
            return {"success": False, "error": "File must be in CSV format."}

        # Try to decode content (common issue is encoding)
        try:
            content = file_content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content = file_content_bytes.decode('latin-1')  # Try another common encoding
            except UnicodeDecodeError:
                return {"success": False,
                        "error": "Could not decode CSV content. Please ensure it's UTF-8 or Latin-1 encoded."}

        # Try to read with various delimiters to be more forgiving
        df = None
        delimiters_to_try = [',', ';', '\t']
        for delim in delimiters_to_try:
            try:
                df = pd.read_csv(io.StringIO(content), sep=delim)
                if df.shape[1] > 1:  # Successfully parsed multiple columns
                    break
            except pd.errors.ParserError:
                continue
            except Exception:  # Catch other potential read errors
                continue

        if df is None or df.shape[1] <= 1 and len(
                delimiters_to_try) > 1:  # Check if parsing failed or resulted in single column for common delimiters
            # Try one last time with sniffer if available or default to comma
            try:
                df = pd.read_csv(io.StringIO(content), sep=None, engine='python')  # Python engine can sniff delimiter
            except Exception as e:
                return {"success": False,
                        "error": f"Could not parse the CSV file. Please check the format and delimiter. Error: {e}"}

        if df.empty:
            return {"success": False, "error": "The CSV file is empty."}

        if df.shape[0] > 100000:
            return {"success": False,
                    "error": f"The CSV file contains {df.shape[0]} rows, which exceeds the maximum limit of 100,000 rows."}

        if df.shape[1] > 30:
            return {"success": False,
                    "error": f"The CSV file contains {df.shape[1]} columns, which exceeds the maximum limit of 30 columns."}

        # Sample the data if it's very large (for further processing, not just viz)
        if df.shape[0] > 10000:
            logger.warning(
                f"The dataset '{filename}' is large ({df.shape[0]} rows). Sampling 10,000 rows for processing.")
            df = df.sample(n=10000, random_state=42)

        return {"success": True, "data": df}

    except Exception as e:
        logger.error(f"Error validating CSV file '{filename}': {str(e)}", exc_info=True)
        return {"success": False, "error": f"Error validating CSV file: {str(e)}"}


def process_data(df):
    """
    Process the data after validation (currently basic stats, can be expanded)

    Args:
        df: The validated dataframe

    Returns:
        dict: Processed data and metadata
    """
    basic_stats = {
        "row_count": df.shape[0],
        "column_count": df.shape[1],
        "column_names": list(df.columns)
    }

    return {
        # "dataframe": df, # No need to return the full df here if it's already in validation result
        "stats": basic_stats,
    }


def detect_data_types(df):
    """
    Detect data types of each column in the dataframe

    Args:
        df: The dataframe to analyze

    Returns:
        dict: Mapping of column names to detected data types
    """
    data_types = {}
    if df is None:  # Guard against None DataFrame
        return data_types

    for column in df.columns:
        col_data = df[column].dropna()  # Work with non-null data for detection

        if col_data.empty:  # If all values were NaN
            data_types[column] = "unknown (all null)"
            continue

        # Attempt numeric conversion robustly
        try:
            pd.to_numeric(col_data)  # This checks if conversion is possible
            if set(col_data.unique()).issubset({0, 1, 0.0, 1.0}):  # check before int conversion
                data_types[column] = "boolean"
            elif (col_data % 1 == 0).all():  # Check if all are whole numbers
                data_types[column] = "integer"
            else:
                data_types[column] = "float"
            continue
        except (ValueError, TypeError):  # Not purely numeric
            pass  # Continue to other checks

        # Try to convert to datetime (make it more robust)
        try:
            # Sample a few non-null values to speed up datetime inference
            sample_size = min(len(col_data), 5)
            if pd.to_datetime(col_data.sample(sample_size, random_state=1), errors='raise',
                              infer_datetime_format=True).notna().all():
                # Full check if sample passes
                if pd.to_datetime(col_data, errors='coerce', infer_datetime_format=True).notna().sum() > 0.8 * len(
                        col_data):  # at least 80% are dates
                    data_types[column] = "datetime"
                    continue
        except Exception:  # More general catch for datetime conversion issues
            pass

        # Check if it's likely boolean (string representation)
        # Convert to string first for consistent comparison
        lower_values = col_data.astype(str).str.lower().unique()
        boolean_like_strings = {'true', 'false', 't', 'f', 'yes', 'no', 'y', 'n', '0', '1'}  # '0', '1' as strings
        if set(lower_values).issubset(boolean_like_strings):
            data_types[column] = "boolean"
            continue

        # Default to text/categorical
        # count() gives non-NA count. nunique() on col_data (already dropped NA)
        if not col_data.empty:
            unique_ratio = col_data.nunique() / len(col_data)
            if unique_ratio < 0.2 and col_data.nunique() < 50:  # Less than 20% unique values AND few distinct values
                data_types[column] = "categorical"
            else:
                data_types[column] = "text"
        else:  # Should have been caught by col_data.empty earlier
            data_types[column] = "unknown (all null after processing)"

    return data_types


def check_data_quality(df):
    """
    Check data quality issues

    Args:
        df: The dataframe to check

    Returns:
        dict: Data quality report with issues
    """
    issues = []
    if df is None:  # Guard against None DataFrame
        return {"issues": ["DataFrame is None, cannot check quality."], "has_issues": True}

    # Check for missing values
    missing_data = df.isnull().sum()  # Use isnull() for broader check
    for column, missing_count in missing_data.items():
        if missing_count > 0:
            missing_percentage = (missing_count / len(df)) * 100
            if missing_percentage > 20:  # Threshold for high percentage
                issues.append(
                    f"Column '{column}' has {missing_percentage:.1f}% missing values (exceeds 20% threshold).")
            elif missing_percentage > 0:  # Any missing values
                issues.append(f"Column '{column}' has {missing_percentage:.1f}% missing values.")

    # Check for outliers in numeric columns
    numeric_cols = df.select_dtypes(include=np.number).columns
    for column in numeric_cols:
        # Ensure column has non-NA values before attempting quantile
        if df[column].notna().any():
            q1 = df[column].quantile(0.25)
            q3 = df[column].quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:  # Avoid division by zero or issues with constant columns
                lower_bound = q1 - (1.5 * iqr)
                upper_bound = q3 + (1.5 * iqr)
                outlier_count = ((df[column] < lower_bound) | (df[column] > upper_bound)).sum()
                if outlier_count > 0:
                    outlier_percentage = (outlier_count / len(df)) * 100
                    if outlier_percentage > 5:  # Threshold for high percentage
                        issues.append(
                            f"Column '{column}' has {outlier_percentage:.1f}% potential outliers (based on IQR rule, exceeds 5% threshold).")
                    elif outlier_percentage > 0:  # Any outliers
                        issues.append(
                            f"Column '{column}' has {outlier_percentage:.1f}% potential outliers (based on IQR rule).")
        else:
            issues.append(f"Numeric column '{column}' contains all missing values, outlier check skipped.")

    if not issues:
        issues.append(
            "No major data quality issues detected based on current checks (missing values > 20%, outliers > 5%).")

    return {
        "issues": issues,
        "has_issues": any(issue for issue in issues if "exceeds" in issue or (
                    "potential outliers" in issue and "%" in issue and float(issue.split('%')[0].split()[-1]) > 0))
        # More precise check for actual issues
    }