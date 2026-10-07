function runAll() {
  const HEADER_COLOR = "#1f3864";
  const AGED_THRESHOLD = 5;
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheets = ss.getSheets();
  
  // Helper to find column index (1-based) by header name
  function findColIndex(ws, headerText) {
    const lastCol = ws.getLastColumn();
    if (lastCol === 0) return -1;
    const headers = ws.getRange(1, 1, 1, lastCol).getValues()[0];
    for (let i = 0; i < headers.length; i++) {
      if (String(headers[i]).trim().toLowerCase() === headerText.toLowerCase()) {
        return i + 1; 
      }
    }
    return -1;
  }
  
  function isBreakSheet(ws) {
    return findColIndex(ws, "Severity") > 0 && findColIndex(ws, "Break Id") > 0;
  }

  // 1 & 2: Format break sheets and highlight aged breaks
  for (let s = 0; s < sheets.length; s++) {
    const ws = sheets[s];
    if (isBreakSheet(ws)) {
      const lastRow = ws.getLastRow();
      const lastCol = ws.getLastColumn();
      
      if (lastRow < 1 || lastCol < 1) continue;
      
      // Format headers
      const headerRange = ws.getRange(1, 1, 1, lastCol);
      headerRange.setFontWeight("bold");
      headerRange.setFontColor("white");
      headerRange.setBackground(HEADER_COLOR);
      headerRange.setHorizontalAlignment("center");
      
      // Apply Filter
      if (ws.getFilter()) {
        ws.getFilter().remove();
      }
      headerRange.createFilter();
      
      const sevCol = findColIndex(ws, "Severity");
      const ageCol = findColIndex(ws, "Age Days");
      
      if (lastRow > 1) {
        const dataRange = ws.getRange(2, 1, lastRow - 1, lastCol);
        const values = dataRange.getValues();
        
        // Prepare formatting arrays
        const backgrounds = dataRange.getBackgrounds();
        const fontWeights = dataRange.getFontWeights();
        const fontColors = dataRange.getFontColors();
        
        for (let r = 0; r < values.length; r++) {
          // Color Severity
          if (sevCol > 0) {
            const sev = String(values[r][sevCol - 1]).toUpperCase();
            if (sev === "HIGH") backgrounds[r][sevCol - 1] = "#f8cbad";
            else if (sev === "MEDIUM") backgrounds[r][sevCol - 1] = "#ffe699";
            else if (sev === "LOW") backgrounds[r][sevCol - 1] = "#c6e0b4";
          }
          
          // Highlight Aged Breaks
          if (ageCol > 0) {
            const ageVal = values[r][ageCol - 1];
            if (typeof ageVal === "number" && ageVal > AGED_THRESHOLD) {
              for (let c = 0; c < lastCol; c++) {
                fontWeights[r][c] = "bold";
              }
              fontColors[r][ageCol - 1] = "#c00000";
            }
          }
        }
        
        dataRange.setBackgrounds(backgrounds);
        dataRange.setFontWeights(fontWeights);
        dataRange.setFontColors(fontColors);
      }
    }
  }

  // 3: Build Team Summary
  const allBreaksWs = ss.getSheetByName("All Breaks");
  if (allBreaksWs) {
    const teamCol = findColIndex(allBreaksWs, "Owner Team");
    const notCol = findColIndex(allBreaksWs, "Notional Usd");
    
    if (teamCol > 0 && notCol > 0) {
      const lastRow = allBreaksWs.getLastRow();
      let counts = {};
      let notional = {};
      
      if (lastRow > 1) {
        const values = allBreaksWs.getRange(2, 1, lastRow - 1, allBreaksWs.getLastColumn()).getValues();
        for (let r = 0; r < values.length; r++) {
          const team = String(values[r][teamCol - 1]);
          const notVal = Number(values[r][notCol - 1]) || 0;
          
          counts[team] = (counts[team] || 0) + 1;
          notional[team] = (notional[team] || 0) + notVal;
        }
      }
      
      let summaryWs = ss.getSheetByName("Team Summary (VBA)");
      if (summaryWs) ss.deleteSheet(summaryWs);
      summaryWs = ss.insertSheet("Team Summary (VBA)", ss.getNumSheets());
      
      const header = ["Owner Team", "Break Lines", "Notional (USD, all lines)"];
      summaryWs.getRange("A1:C1").setValues([header])
                                 .setFontWeight("bold")
                                 .setFontColor("white")
                                 .setBackground(HEADER_COLOR);
      
      let outData = [];
      for (let team in counts) {
        outData.push([team, counts[team], notional[team]]);
      }
      
      if (outData.length > 0) {
        summaryWs.getRange(2, 1, outData.length, 3).setValues(outData);
        summaryWs.getRange(2, 3, outData.length, 1).setNumberFormat("#,##0.00");
      }
      summaryWs.autoResizeColumns(1, 3);
    }
    
    // 4: Export High Severity
    const sevCol = findColIndex(allBreaksWs, "Severity");
    if (sevCol > 0) {
      let highSevWs = ss.getSheetByName("High Severity");
      if (highSevWs) ss.deleteSheet(highSevWs);
      highSevWs = ss.insertSheet("High Severity", ss.getNumSheets());
      
      const lastRow = allBreaksWs.getLastRow();
      const lastCol = allBreaksWs.getLastColumn();
      
      const headerVals = allBreaksWs.getRange(1, 1, 1, lastCol).getValues();
      highSevWs.getRange(1, 1, 1, lastCol).setValues(headerVals)
                                          .setFontWeight("bold")
                                          .setFontColor("white")
                                          .setBackground(HEADER_COLOR);
      
      if (lastRow > 1) {
        const values = allBreaksWs.getRange(2, 1, lastRow - 1, lastCol).getValues();
        let highSevData = [];
        
        for (let r = 0; r < values.length; r++) {
          if (String(values[r][sevCol - 1]).toUpperCase() === "HIGH") {
            highSevData.push(values[r]);
          }
        }
        
        if (highSevData.length > 0) {
          highSevWs.getRange(2, 1, highSevData.length, lastCol).setValues(highSevData);
        }
      }
      for (let c = 1; c <= lastCol; c++) {
        highSevWs.autoResizeColumn(c);
      }
    }
  }
}
