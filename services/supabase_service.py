class SupabaseService:
    def __init__(self, url="", anon_key="", bucket="videos"):
        self.url = url
        self.anon_key = anon_key
        self.bucket = bucket

    @property
    def configured(self):
        return bool(self.url and self.anon_key)

    def status(self):
        return {
            "configured": self.configured,
            "bucket": self.bucket,
            "message": (
                "Supabase placeholders configured"
                if self.configured
                else "Local JSON storage active; Supabase not configured"
            ),
        }
