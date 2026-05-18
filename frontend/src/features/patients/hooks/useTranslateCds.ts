import { useCallback, useState } from "react";
import { cdsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

const SPLIT_MARKER = "\n\n<<<SEP>>>\n\n";

export function useTranslateCds() {
  const [amharicSummary, setAmharicSummary] = useState<string | undefined>();
  const [amharicContent, setAmharicContent] = useState<string | undefined>();
  const [isPending, setIsPending] = useState(false);
  const [isAmharic, setIsAmharic] = useState(false);

  const translate = useCallback(async (summary: string, content: string) => {
    if (amharicContent) {
      setIsAmharic(true);
      return;
    }
    setIsPending(true);
    try {
      // Send summary and sections in one request, split on a marker the model
      // will preserve (it is not a translatable phrase).
      const combined = `${summary}${SPLIT_MARKER}${content}`;
      const { data } = await cdsClient.post<{ translatedContent: string }>("/translate", {
        content: combined,
        language: "Amharic",
      });
      const parts = data.translatedContent.split("<<<SEP>>>");
      setAmharicSummary((parts[0] ?? "").trim());
      setAmharicContent((parts[1] ?? data.translatedContent).trim());
      setIsAmharic(true);
    } catch (error) {
      toast.error("Translation failed", describeError(error));
    } finally {
      setIsPending(false);
    }
  }, [amharicContent]);

  const backToEnglish = useCallback(() => {
    setIsAmharic(false);
  }, []);

  return { isAmharic, amharicSummary, amharicContent, isPending, translate, backToEnglish };
}
