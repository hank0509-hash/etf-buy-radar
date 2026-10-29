# ETF 買進共識雷達 — 每天自動更新版

此版本沿用你已跑成功的 **V6**，透過 GitHub Actions 每天 **台灣時間 23:27** 自動執行、保存結果和每日歷史、更新固定的 GitHub Pages 網址。

> 這是完整程式包，但**下載本檔案不會直接啟動排程**。第一次須放到你自己的 GitHub 儲存庫並開啟 Pages / Actions。

## 一次性設定（不需要每天再使用 Colab）

1. 建立或登入 [GitHub](https://github.com/) 帳號。打開 https://github.com/new 建立 **Public** repository，建議名稱 `etf-buy-radar`，預設分支 `main`。（公開庫的程式及結果會公開，勿放私人憑證。）
2. 將本 ZIP 解壓縮。**要把解壓後資料夾裡面的檔案和 `.github/workflows/` 資料夾都上傳到 repository 根目錄**，不可只上傳 ZIP；`.github/workflows/daily-etf-radar.yml` 必須存在於 `main` 分支。若網頁上傳沒有包含隱藏的 `.github`，在 GitHub 按 **Add file → Create new file**，以 `.github/workflows/daily-etf-radar.yml` 為檔名，貼入 ZIP 中對應檔案的內容。
3. 在 repository 的 **Settings → Pages → Build and deployment → Source**，選擇 **GitHub Actions**。
4. 前往 **Actions → 每日 ETF 買進共識雷達 → Run workflow**，先手動執行一次。若出現 Actions 權限要求，依 GitHub 畫面啟用（或檢查 Settings → Actions → General）。
5. 第一次成功後，**Settings → Pages** 會顯示固定網址，通常為 `https://你的帳號.github.io/etf-buy-radar/`。以該頁面為主，不必再下載 Colab。

## 自動執行內容

- 每天 23:27（台灣）排程；時間依 GitHub 系統實際觸發，可能延遲或略過。
- 重新讀公開主動 ETF 的**持股異動**資料、被動指數**已公告但尚未生效**的納入異動。
- 更新 `site/index.html`、`site/latest.csv`、`site/results.zip`、`site/status.json`。
- 保存 `site/history/YYYY-MM-DD.csv`，日後能比較各天榜單。
- 若所有可用訊號都失敗或無效，當次工作會失敗，**不會用空白假榜單覆蓋舊的結果**；可到 Actions 看錯誤紀錄。

## 注意事項

- 主動式未來兩日的金額為**未校準的續買情境**，不是基金經理人已預先下單的數字。
- 被動式公告新增不等於 ETF 還未完成買入；無可靠目標權重時金額留白。
- 來源覆蓋有限，並不涵蓋所有 ETF；最新主動股數異動日與剩餘未核對公告數在網頁顯示。
- 使用 GitHub Pages 發布時，**公開 repo 內的內容會公開**；不要上傳 API 金鑰、個資、金融帳號。
- GitHub 的定時任務可能因流量延遲/跳過；公共倉庫若 60 天無活動，排程可能遭停用，須進 Actions 檢查。見 https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows 。
- 本套件的 GitHub 端執行與即時外部來源下載，**尚未在你的 GitHub 帳號驗證**；已完成離線程式與輸出測試。
