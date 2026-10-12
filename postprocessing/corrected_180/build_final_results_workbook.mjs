import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dataRoot = path.join(root, "data");
const releaseRoot = path.resolve(root, "..", "Master-Thesis-GitHub-Release");
const templatePath = path.join(releaseRoot, "results", "Thesis_Result_Tables.xlsx");
const outputPath = templatePath;
const previewDir = path.join(process.env.TEMP || process.env.TMP || root, "final_thesis_workbook_previews");

const sheets = [
  ["Regional gates", "Final 60/120-satellite completion gates", "final_results/completion_gate.csv"],
  ["180 gate", "Verified India 180-satellite scope gate", "final_corrected_180/corrected_180_scope_gate.csv"],
  ["Regional deltas", "Matched California-India effects", "final_results/uniform_60_120_matched_region_delta_summary.csv"],
  ["CN response", "Matched regional Central Node response", "final_results/uniform_60_120_cn_absolute_count_summary.csv"],
  ["Factor effects", "Matched regional architecture-factor effects", "final_results/uniform_60_120_factor_effects.csv"],
  ["Rank agreement", "Cross-window rank agreement", "final_results/uniform_60_120_regional_rank_agreement.csv"],
  ["Metric redundancy", "Objective-metric redundancy audit", "final_results/uniform_60_120_metric_pair_redundancy.csv"],
  ["Main shortlist", "Cross-window architecture shortlist", "final_results/uniform_60_120_conditional_top5_shortlist.csv"],
  ["Rank acceptability", "Matched regional rank acceptability", "final_results/uniform_60_120_rank_acceptability.csv"],
  ["Robustness", "Matched regional robustness summary", "final_results/uniform_60_120_robustness_summary.csv"],
  ["Scenario effects", "Matched regional policy and stress effects", "final_results/uniform_60_120_paired_scenario_effects_summary.csv"],
  ["Priority timing", "Priority-stratified event-time summary", "final_results/priority_stratified_event_time_summary.csv"],
  ["180 CN response", "India 180-satellite Central Node response", "final_corrected_180/corrected_180_cn_absolute_count_summary.csv"],
  ["120-180 effects", "Matched India 120-to-180 effects", "final_corrected_180/corrected_180_matched_120_180_summary.csv"],
  ["180 robustness", "India 180-satellite robustness summary", "final_corrected_180/corrected_180_robustness_summary.csv"],
  ["180 policy effects", "India 180-satellite routing and stress effects", "final_corrected_180/corrected_180_paired_scenario_effects_summary.csv"],
  ["180 priority", "India 180-satellite priority summary", "final_corrected_180/corrected_180_priority_equal_weight_summary.csv"],
  ["180 shortlist", "India 180-satellite architecture shortlist", "final_corrected_180/corrected_180_conditional_top5_shortlist.csv"],
];

function columnName(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function typed(value) {
  if (value === null || value === undefined || value === "") return null;
  const text = String(value).trim();
  if (/^(true|false)$/i.test(text)) return text.toLowerCase() === "true";
  if (/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return Number(text);
  return text;
}

async function loadCsv(relative) {
  const csvPath = path.join(dataRoot, ...relative.split("/"));
  const text = await fs.readFile(csvPath, "utf8");
  const temporary = await Workbook.fromCSV(text, { sheetName: "Data" });
  const values = temporary.worksheets.getItem("Data").getUsedRange(true).values;
  return values
    .map((row) => row.map(typed))
    .filter((row) => row.some((value) => value !== null));
}

function applyCommonStyle(sheet, usedRange) {
  sheet.showGridLines = false;
  usedRange.format.font = { name: "Arial", size: 10, color: "#1F2937" };
  usedRange.format.verticalAlignment = "center";
}

function formatDataSheet(sheet, title, source, values, index) {
  const columns = Math.max(...values.map((row) => row.length));
  const rows = values.length;
  const lastColumn = columnName(columns - 1);
  sheet.getRange(`A1:${lastColumn}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A1").format = {
    font: { name: "Arial", size: 14, bold: true, color: "#123B5D" },
    rowHeight: 25,
  };
  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[`Source: results/${source}`]];
  sheet.getRange("A2").format = {
    font: { name: "Arial", size: 9, italic: true, color: "#4B5563" },
    rowHeight: 19,
  };
  sheet.getRange("A4").write(values);
  const used = sheet.getRange(`A1:${lastColumn}${rows + 3}`);
  applyCommonStyle(sheet, used);
  sheet.getRange("A1").format = {
    font: { name: "Arial", size: 14, bold: true, color: "#123B5D" },
    rowHeight: 25,
  };
  sheet.getRange("A2").format = {
    font: { name: "Arial", size: 9, italic: true, color: "#4B5563" },
    rowHeight: 19,
  };
  const header = sheet.getRange(`A4:${lastColumn}4`);
  header.format = {
    fill: "#1F4E78",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 34,
    borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
  };
  if (rows > 1) {
    const body = sheet.getRange(`A5:${lastColumn}${rows + 3}`);
    body.format.borders = {
      insideHorizontal: { style: "thin", color: "#D9E2F3" },
      bottom: { style: "thin", color: "#AAB8C2" },
    };
  }
  const headers = values[0].map((value) => String(value || ""));
  for (let col = 0; col < columns; col += 1) {
    const letter = columnName(col);
    const headerText = headers[col].toLowerCase();
    const width = Math.min(36, Math.max(12, ...values.slice(0, 80).map((row) => String(row[col] ?? "").length + 2)));
    sheet.getRange(`${letter}4:${letter}${rows + 3}`).format.columnWidth = width;
    if (/count|cases|rows|tasks|replicates|identifiers|architectures|n_/.test(headerText)) {
      sheet.getRange(`${letter}5:${letter}${rows + 3}`).format.numberFormat = "#,##0";
    } else if (/percent|mean|median|delta|low|high|auc|min|mbit|traffic|completion|coverage|observation/.test(headerText)) {
      sheet.getRange(`${letter}5:${letter}${rows + 3}`).format.numberFormat = "#,##0.00";
    }
  }
  sheet.freezePanes.freezeRows(4);
  sheet.freezePanes.freezeColumns(1);
  const table = sheet.tables.add(`A4:${lastColumn}${rows + 3}`, true, `ResultsTable${index + 1}`);
  table.showFilterButton = true;
  return { rows: rows - 1, columns, lastColumn };
}

async function main() {
  await fs.mkdir(previewDir, { recursive: true });

  if (await fs.stat(templatePath).then(() => true).catch(() => false)) {
    const sourceBlob = await FileBlob.load(templatePath);
    const sourceWorkbook = await SpreadsheetFile.importXlsx(sourceBlob);
    const sourcePreview = await sourceWorkbook.render({ sheetName: "Summary", range: "A1:F16", scale: 1.4, format: "png" });
    await fs.writeFile(path.join(previewDir, "source_summary.png"), new Uint8Array(await sourcePreview.arrayBuffer()));
  }

  const workbook = Workbook.create();
  const summary = workbook.worksheets.add("Summary");
  summary.showGridLines = false;
  summary.getRange("A1:F1").merge();
  summary.getRange("A1").values = [["Master Thesis Results"]];
  summary.getRange("A1").format = {
    font: { name: "Arial", size: 16, bold: true, color: "#123B5D" },
    rowHeight: 28,
  };
  summary.getRange("A2:F2").merge();
  summary.getRange("A2").values = [["Final evidence release | 14 October 2026"]];
  summary.getRange("A2").format = { font: { name: "Arial", size: 10, italic: true, color: "#4B5563" } };
  const summaryRows = [
    ["Evidence block", "Population", "Analysis role", "Key finding", "Inference boundary", "Status"],
    ["Matched regional", "594 California + 594 India cases; 60/120 satellites", "Cross-window effects and main shortlist", "India: +4.66 pp observation; -2.98 pp strict completion; -5.95 pp useful coverage; about 175 min later worst-case dissemination", "Selected California and India event windows", "Final"],
    ["Verified India 180", "282 complete cases; 773 tasks; 216 balanced CN30-CN100 comparisons", "Large-fleet response, robustness, priority, Pareto, and matched 120-to-180 effects", "120-to-180: +1.23 pp observation, +3.99 pp strict completion, -44.68 min P100, +25,827 Mbit per case", "India workload; balanced CN30-CN100 block", "Final"],
    ["Reference Campaign", "Historical nominal California-India campaign", "Mechanism baseline and context", "Central Node benefit is non-monotonic under bounded routing", "Not pooled numerically with Fresh V2", "Historical baseline"],
  ];
  summary.getRange("A4").write(summaryRows);
  summary.getRange("A4:F4").format = {
    fill: "#1F4E78",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 30,
  };
  summary.getRange("A5:F7").format = {
    font: { name: "Arial", size: 10, color: "#1F2937" },
    verticalAlignment: "top",
    wrapText: true,
    rowHeight: 60,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  summary.getRange("A9:F9").merge();
  summary.getRange("A9").values = [["Decision conclusions"]];
  summary.getRange("A9").format = {
    fill: "#D9EAF7",
    font: { name: "Arial", size: 12, bold: true, color: "#123B5D" },
    rowHeight: 24,
  };
  const decisions = [
    ["Finding", "Evidence"],
    ["Central Nodes help conditionally", "Performance depends on Central Node fraction, forwarding policy, and workload; the response is not monotonic."],
    ["Communication has a cost", "Mission gains can saturate while differentiated-node count and transferred traffic continue to grow."],
    ["Robustness changes the choice", "Ground-station outage is the largest tested individual vulnerability; combined stresses reveal losses that nominal analysis misses."],
    ["No universal optimum exists", "Exact Pareto filtering and rank-acceptability analysis identify portfolios rather than one preference-free winner."],
  ];
  summary.getRange("A10").write(decisions);
  summary.getRange("A10:B10").format = {
    fill: "#1F4E78",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
  };
  summary.getRange("A11:B14").format = {
    font: { name: "Arial", size: 10, color: "#1F2937" },
    wrapText: true,
    verticalAlignment: "top",
    rowHeight: 38,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  [24, 34, 40, 50, 44, 18].forEach((width, index) => {
    const letter = columnName(index);
    summary.getRange(`${letter}1:${letter}14`).format.columnWidth = width;
  });
  summary.freezePanes.freezeRows(4);
  summary.tabColor = "#1F4E78";

  const guideRows = [["Sheet", "Canonical CSV", "Rows", "Purpose"]];
  for (let index = 0; index < sheets.length; index += 1) {
    const [name, title, relative] = sheets[index];
    const values = await loadCsv(relative);
    const sheet = workbook.worksheets.add(name);
    const details = formatDataSheet(sheet, title, relative, values, index);
    guideRows.push([name, `results/${relative}`, details.rows, title]);
  }

  const guide = workbook.worksheets.add("Data guide");
  guide.showGridLines = false;
  guide.getRange("A1:D1").merge();
  guide.getRange("A1").values = [["Workbook data guide"]];
  guide.getRange("A1").format = { font: { name: "Arial", size: 14, bold: true, color: "#123B5D" } };
  guide.getRange("A3").write(guideRows);
  guide.getRange("A3:D3").format = {
    fill: "#1F4E78",
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    rowHeight: 28,
  };
  guide.getRange(`A4:D${guideRows.length + 2}`).format = {
    font: { name: "Arial", size: 10, color: "#1F2937" },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  [22, 62, 10, 52].forEach((width, index) => {
    const letter = columnName(index);
    guide.getRange(`${letter}1:${letter}${guideRows.length + 2}`).format.columnWidth = width;
  });
  guide.freezePanes.freezeRows(3);
  guide.tabColor = "#7F8C8D";

  workbook.recalculate();
  const summaryCheck = await workbook.inspect({
    kind: "table",
    range: "Summary!A1:F14",
    include: "values,formulas",
    tableMaxRows: 20,
    tableMaxCols: 8,
    maxChars: 8000,
  });
  console.log(summaryCheck.ndjson);
  const errorCheck = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  console.log(errorCheck.ndjson);

  const renderTargets = ["Summary", ...sheets.map(([name]) => name), "Data guide"];
  for (const sheetName of renderTargets) {
    const sheet = workbook.worksheets.getItem(sheetName);
    const used = sheet.getUsedRange(true);
    const rowCount = Math.min(used.rowCount, 35);
    const columnCount = used.columnCount;
    const range = `A1:${columnName(columnCount - 1)}${rowCount}`;
    const preview = await workbook.render({ sheetName, range, scale: 0.8, format: "png" });
    const safeName = sheetName.replace(/[^A-Za-z0-9]+/g, "_").toLowerCase();
    await fs.writeFile(path.join(previewDir, `${safeName}.png`), new Uint8Array(await preview.arrayBuffer()));
  }

  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
  console.log(`Saved ${outputPath}`);
}

await main();
