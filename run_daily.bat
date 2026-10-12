@echo off
chcp 65001 > nul
REM 毎朝の情報収集とレポート作成をまとめて実行する（Windows用）
REM ダブルクリック、またはタスクスケジューラから実行する

REM このファイルがあるフォルダに移動する
cd /d %~dp0

REM 日本語をファイルに書き出すときに文字化けで止まらないようにする
set PYTHONUTF8=1

echo [1/3] YouTubeの動画を集めています...
py src\youtube_trends.py
echo [2/3] ニュースを集めています...
py src\news_feed.py
echo [3/3] レポートを作っています...
py src\make_report.py

echo 完了しました。reports フォルダを見てください。

REM ダブルクリックしたときは画面がすぐ閉じないように止める（自動実行では止めない）
if /i not "%~1"=="auto" pause
