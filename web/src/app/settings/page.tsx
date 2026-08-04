"use client";

import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { GoudanLogo } from "@/components/brand/GoudanLogo";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { Sun, Moon, LogOut, User } from "lucide-react";

export default function SettingsPage() {
  const router = useRouter();
  const { theme, setTheme, resolvedTheme } = useTheme();
  const { auth: authRepo } = useRepositories();
  const { user, logout } = useAuthStore();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const handleLogout = async () => {
    await logout(authRepo);
    router.replace("/login");
  };

  return (
    <AppShell>
      <div className="h-full overflow-y-auto px-4 py-6 max-w-2xl mx-auto space-y-6">
        {/* User info */}
        <Card className="bg-surface">
          <CardHeader className="pb-2 pt-4 px-4">
            <CardTitle className="text-sm">用户信息</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-full bg-secondary">
                <User className="h-5 w-5 text-muted-foreground" />
              </div>
              <div>
                <p className="text-sm font-medium">{user?.name ?? "苟蛋用户"}</p>
                <p className="text-xs text-muted-foreground">
                  {user?.email ?? "user@goudan.app"}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Theme */}
        <Card className="bg-surface">
          <CardHeader className="pb-2 pt-4 px-4">
            <CardTitle className="text-sm">主题设置</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4">
            <div className="flex items-center justify-between">
              <Label className="text-sm text-muted-foreground">当前主题</Label>
              <div className="flex items-center gap-1 rounded-md border border-border p-0.5">
                <Button
                  variant={mounted && resolvedTheme === "dark" ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setTheme("dark")}
                  className="gap-1.5"
                >
                  <Moon className="h-3.5 w-3.5" />
                  深色
                </Button>
                <Button
                  variant={mounted && resolvedTheme === "light" ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setTheme("light")}
                  className="gap-1.5"
                >
                  <Sun className="h-3.5 w-3.5" />
                  浅色
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Logout */}
        <Card className="bg-surface">
          <CardContent className="px-4 py-4">
            <Button
              variant="outline"
              className="w-full text-destructive hover:text-destructive"
              onClick={handleLogout}
            >
              <LogOut className="h-4 w-4 mr-2" />
              退出登录
            </Button>
          </CardContent>
        </Card>

        {/* Brand */}
        <div className="flex justify-center py-4">
          <GoudanLogo size={24} />
        </div>
      </div>
    </AppShell>
  );
}
