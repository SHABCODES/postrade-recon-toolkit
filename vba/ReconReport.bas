Attribute VB_Name = "ReconReport"
Option Explicit
'===============================================================================
' ReconReport.bas - macros for the post-trade reconciliation workbook
'
' Import: Excel > Alt+F11 > File > Import File... > ReconReport.bas
' Save the workbook as .xlsm, then run RunAll from Alt+F8.
'
' Works on the sheets written by postrade_recon (header row 1, columns such as
' "Severity", "Age Days", "Owner Team", "Notional Usd" on every break sheet).
'===============================================================================

Private Const HEADER_COLOR As Long = 6567967    ' RGB(31,56,100) navy
Private Const AGED_THRESHOLD As Long = 5        ' business days

Public Sub RunAll()
    Application.ScreenUpdating = False
    FormatBreakSheets
    HighlightAgedBreaks AGED_THRESHOLD
    BuildTeamSummary
    ExportHighSeverity
    Application.ScreenUpdating = True
    MsgBox "Reconciliation report formatted. See 'Team Summary (VBA)' and 'High Severity'.", vbInformation
End Sub

' Finds a header by name in row 1; returns 0 if the column does not exist.
Private Function FindCol(ws As Worksheet, headerText As String) As Long
    Dim c As Long, lastCol As Long
    lastCol = ws.Cells(1, ws.Columns.Count).End(xlToLeft).Column
    For c = 1 To lastCol
        If StrComp(Trim$(CStr(ws.Cells(1, c).Value)), headerText, vbTextCompare) = 0 Then
            FindCol = c
            Exit Function
        End If
    Next c
    FindCol = 0
End Function

Private Function IsBreakSheet(ws As Worksheet) As Boolean
    IsBreakSheet = (FindCol(ws, "Severity") > 0 And FindCol(ws, "Break Id") > 0)
End Function

' 1) Consistent header style, frozen header, filters and severity colours on every break sheet.
Public Sub FormatBreakSheets()
    Dim ws As Worksheet, sevCol As Long, lastRow As Long, lastCol As Long, r As Long
    For Each ws In ThisWorkbook.Worksheets
        If IsBreakSheet(ws) Then
            lastCol = ws.Cells(1, ws.Columns.Count).End(xlToLeft).Column
            lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
            With ws.Range(ws.Cells(1, 1), ws.Cells(1, lastCol))
                .Font.Bold = True
                .Font.Color = vbWhite
                .Interior.Color = HEADER_COLOR
                .HorizontalAlignment = xlCenter
            End With
            If ws.AutoFilterMode Then ws.AutoFilterMode = False
            ws.Range(ws.Cells(1, 1), ws.Cells(1, lastCol)).AutoFilter
            sevCol = FindCol(ws, "Severity")
            For r = 2 To lastRow
                Select Case UCase$(CStr(ws.Cells(r, sevCol).Value))
                    Case "HIGH":   ws.Cells(r, sevCol).Interior.Color = RGB(248, 203, 173)
                    Case "MEDIUM": ws.Cells(r, sevCol).Interior.Color = RGB(255, 230, 153)
                    Case "LOW":    ws.Cells(r, sevCol).Interior.Color = RGB(198, 224, 180)
                End Select
            Next r
        End If
    Next ws
End Sub

' 2) Bold red text on every break older than the threshold (business days).
Public Sub HighlightAgedBreaks(ByVal thresholdDays As Long)
    Dim ws As Worksheet, ageCol As Long, lastRow As Long, lastCol As Long, r As Long, aged As Long
    For Each ws In ThisWorkbook.Worksheets
        If IsBreakSheet(ws) Then
            ageCol = FindCol(ws, "Age Days")
            lastCol = ws.Cells(1, ws.Columns.Count).End(xlToLeft).Column
            lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
            For r = 2 To lastRow
                If IsNumeric(ws.Cells(r, ageCol).Value) And Not IsEmpty(ws.Cells(r, ageCol).Value) Then
                    If CLng(ws.Cells(r, ageCol).Value) > thresholdDays Then
                        ws.Range(ws.Cells(r, 1), ws.Cells(r, lastCol)).Font.Bold = True
                        ws.Cells(r, ageCol).Font.Color = RGB(192, 0, 0)
                        If ws.Name = "All Breaks" Then aged = aged + 1
                    End If
                End If
            Next r
        End If
    Next ws
    Debug.Print "Breaks aged > " & thresholdDays & " business days: " & aged
End Sub

' 3) Independent team summary built in VBA (cross-check against the formula-driven Summary sheet).
Public Sub BuildTeamSummary()
    Dim src As Worksheet, dst As Worksheet, teamCol As Long, notCol As Long, lastRow As Long
    Dim counts As Object, notional As Object, r As Long, key As Variant, outRow As Long
    Set src = ThisWorkbook.Worksheets("All Breaks")
    teamCol = FindCol(src, "Owner Team")
    notCol = FindCol(src, "Notional Usd")
    lastRow = src.Cells(src.Rows.Count, 1).End(xlUp).Row
    Set counts = CreateObject("Scripting.Dictionary")
    Set notional = CreateObject("Scripting.Dictionary")
    For r = 2 To lastRow
        key = CStr(src.Cells(r, teamCol).Value)
        counts(key) = counts(key) + 1
        notional(key) = notional(key) + CDbl(src.Cells(r, notCol).Value)
    Next r

    Application.DisplayAlerts = False
    On Error Resume Next
    ThisWorkbook.Worksheets("Team Summary (VBA)").Delete
    On Error GoTo 0
    Application.DisplayAlerts = True
    Set dst = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
    dst.Name = "Team Summary (VBA)"
    dst.Range("A1:C1").Value = Array("Owner Team", "Break Lines", "Notional (USD, all lines)")
    outRow = 2
    For Each key In counts.Keys
        dst.Cells(outRow, 1).Value = key
        dst.Cells(outRow, 2).Value = counts(key)
        dst.Cells(outRow, 3).Value = notional(key)
        dst.Cells(outRow, 3).NumberFormat = "#,##0.00"
        outRow = outRow + 1
    Next key
    With dst.Range("A1:C1")
        .Font.Bold = True: .Font.Color = vbWhite: .Interior.Color = HEADER_COLOR
    End With
    dst.Columns("A:C").AutoFit
End Sub

' 4) Copy every HIGH severity line to its own sheet for the morning call.
Public Sub ExportHighSeverity()
    Dim src As Worksheet, dst As Worksheet, sevCol As Long, lastRow As Long, lastCol As Long
    Dim r As Long, outRow As Long
    Set src = ThisWorkbook.Worksheets("All Breaks")
    sevCol = FindCol(src, "Severity")
    lastRow = src.Cells(src.Rows.Count, 1).End(xlUp).Row
    lastCol = src.Cells(1, src.Columns.Count).End(xlToLeft).Column

    Application.DisplayAlerts = False
    On Error Resume Next
    ThisWorkbook.Worksheets("High Severity").Delete
    On Error GoTo 0
    Application.DisplayAlerts = True
    Set dst = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
    dst.Name = "High Severity"
    src.Range(src.Cells(1, 1), src.Cells(1, lastCol)).Copy dst.Range("A1")
    outRow = 2
    For r = 2 To lastRow
        If UCase$(CStr(src.Cells(r, sevCol).Value)) = "HIGH" Then
            src.Range(src.Cells(r, 1), src.Cells(r, lastCol)).Copy dst.Cells(outRow, 1)
            outRow = outRow + 1
        End If
    Next r
    dst.Columns.AutoFit
    Application.CutCopyMode = False
End Sub
